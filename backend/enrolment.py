"""Enrolment through the assistant (#32): the voiceprint must come from the audio the check will see (the orb's,
noise suppressed), so the running bot records it. enroll.py drives these routes; while active, Parakeet hands
every segment here instead of to the pipeline, so the assistant doesn't react to the lines."""

import hmac
import json
import re
import threading
import time

from fastapi import APIRouter, Header, HTTPException
from loguru import logger

from voiceprint import (MIN_SPEECH_SECS, build_vector, delete_voiceprint, learn_spellings, learned_path,
                        WAKE_PREFIXES, learned_wake_phrases, save_voiceprint, speech_only)

# The wake phrase with a short request, as the owner really wakes it (#32): heard right 9 times in 10, where a
# bare "Hey Nova" was heard right 3 times in 8.
LINES = ["Hey {name}, what time is it?", "Hey {name}, what's the weather tomorrow?",
         "Hey {name}, remind me to call mom at six.", "Hey {name}, find me a cheap flight.",
         "Hey {name}, what am I working on?"]
EXPIRE_SECS = 60  # enroll.py killed mid-way: the assistant hears normally again after this


class Enrolment:
    def __init__(self, cfg, voiceprint, embed, cohort, on_learned=None, on_deleted=None, clock=time.monotonic):
        self.cfg, self._vp, self._embed, self._cohort = cfg, voiceprint, embed, cohort
        self.on_learned, self.on_deleted, self._clock = on_learned, on_deleted, clock
        self._lock = threading.Lock()  # offer() runs in the STT's worker thread
        self._takes, self._since, self._done, self._learned, self._error = None, 0.0, False, [], None
        self._finishing = False  # all takes in, the voiceprint being built
        self._rejected = 0  # segments not heard as the wake phrase: a cough, another voice, a relay note

    @property
    def ready(self) -> bool:
        return self._embed is not None

    @property
    def active(self) -> bool:
        with self._lock:
            if self._takes is not None and self._clock() - self._since > EXPIRE_SECS:
                self._takes = None
            return self._takes is not None

    def start(self):
        with self._lock:
            self._takes, self._since, self._done, self._learned, self._error = [], self._clock(), False, [], None
            self._rejected = 0
        logger.info("enrolment started")

    def cancel(self):
        with self._lock:
            self._takes = None

    def offer(self, pcm: bytes, text: str) -> None:
        with self._lock:
            if self._takes is None:
                return
            x = speech_only(pcm)
            if len(x) < MIN_SPEECH_SECS * 16000:
                return
            if not self._is_take(text):
                self._rejected += 1
                return
            self._takes.append((x, text))
            self._since = self._clock()
            if len(self._takes) < len(LINES):
                return
            takes, self._takes, self._finishing = self._takes, None, True
        try:
            self._finish(takes)
        except Exception as e:  # never into the STT: the assistant keeps working, enroll.py reports it
            logger.exception("enrolment failed")
            with self._lock:
                self._error = str(e) or type(e).__name__
        finally:
            with self._lock:
                self._finishing = False

    def _finish(self, takes) -> None:
        mean = self._cohort()
        vector = build_vector([self._embed(x) for x, _ in takes], mean)
        save_voiceprint(self.cfg, vector, mean)
        self._vp.set(vector, mean)
        learned = learn_spellings([t for _, t in takes], self.cfg.name)  # never kept, only the spellings
        learned_path(self.cfg).write_text(json.dumps(learned))
        with self._lock:
            self._done, self._learned = True, learned
        if self.on_learned:
            self.on_learned(learned)
        logger.info(f"enrolled; learned {len(learned)} wake spelling(s)")

    def _is_take(self, text: str) -> bool:
        """Heard as the wake phrase: starts with a wake prefix, or has the name in it."""
        words = re.sub(r"[^\w\s]", "", text.lower()).split()
        return (any(words[: len(p.split())] == p.split() for p in WAKE_PREFIXES)
                or self.cfg.name.lower().split()[0] in words)

    def status(self) -> dict:
        active = self.active
        with self._lock:
            heard = len(self._takes or []) if active else (len(LINES) if self._done or self._finishing else 0)
            return {"active": active, "heard": heard,
                    "done": self._done, "learned": self._learned, "error": self._error,
                    "rejected": self._rejected}

    def delete(self):
        self.cancel()
        learned = learned_wake_phrases(self.cfg)  # before the file goes: open sessions must drop them too
        delete_voiceprint(self.cfg)
        self._vp.clear()
        if self.on_deleted:
            self.on_deleted(learned)
        logger.info("voiceprint deleted: any voice can wake it again")


def make_enrol_router(enrolment: Enrolment, token: str) -> APIRouter:
    router = APIRouter()

    def check(tok):
        if not tok or not hmac.compare_digest(tok.encode(), token.encode()):
            raise HTTPException(403)

    @router.post("/enrol/start")
    async def start(x_voice_token: str | None = Header(None)):
        check(x_voice_token)
        if not enrolment.ready:
            raise HTTPException(503, "The speaker model is not installed: run ./install.sh")
        enrolment.start()
        return enrolment.status()

    @router.get("/enrol/status")
    async def status(x_voice_token: str | None = Header(None)):
        check(x_voice_token)
        return enrolment.status()

    @router.post("/enrol/cancel")
    async def cancel(x_voice_token: str | None = Header(None)):
        check(x_voice_token)
        enrolment.cancel()
        return enrolment.status()

    @router.post("/enrol/delete")
    async def delete(x_voice_token: str | None = Header(None)):
        check(x_voice_token)
        enrolment.delete()
        return {"deleted": True}

    return router
