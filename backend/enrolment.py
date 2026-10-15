"""Enrolment through Milo (#32): the voiceprint must come from the audio the check will see (the orb's,
noise suppressed), so the running bot records it. enroll.py drives these routes; while active, Parakeet hands
every segment here instead of to the pipeline, so Milo doesn't react to the lines."""

import hmac
import json
import threading
import time

from fastapi import APIRouter, Header, HTTPException
from loguru import logger

from voiceprint import (MIN_SPEECH_SECS, build_vector, delete_voiceprint, learn_spellings, learned_path,
                        save_voiceprint, speech_only)

LINES = ["Hey {name}", "Hey {name}", "Hey {name}", "Book a table for two at eight tonight.",
         "Find me a cheap flight to Bangalore next Friday.", "What am I working on right now?"]
EXPIRE_SECS = 60  # enroll.py killed mid-way: Milo hears normally again after this


class Enrolment:
    def __init__(self, cfg, voiceprint, embed, cohort, on_learned=None, clock=time.monotonic):
        self.cfg, self._vp, self._embed, self._cohort = cfg, voiceprint, embed, cohort
        self.on_learned, self._clock = on_learned, clock
        self._lock = threading.Lock()  # offer() runs in the STT's worker thread
        self._takes, self._since, self._done, self._learned, self._error = None, 0.0, False, [], None

    @property
    def active(self) -> bool:
        with self._lock:
            if self._takes is not None and self._clock() - self._since > EXPIRE_SECS:
                self._takes = None
            return self._takes is not None

    def start(self):
        with self._lock:
            self._takes, self._since, self._done, self._learned, self._error = [], self._clock(), False, [], None
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
            self._takes.append((x, text))
            self._since = self._clock()
            if len(self._takes) < len(LINES):
                return
            takes, self._takes = self._takes, None
        try:
            self._finish(takes)
        except Exception as e:  # never into the STT: Milo keeps working, enroll.py reports it
            logger.exception("enrolment failed")
            with self._lock:
                self._error = str(e) or type(e).__name__

    def _finish(self, takes) -> None:
        mean = self._cohort()
        vector = build_vector([self._embed(x) for x, _ in takes], mean)
        save_voiceprint(self.cfg, vector, mean)
        self._vp.set(vector, mean)
        learned = learn_spellings([t for _, t in takes[:3]], self.cfg.name)  # never kept, only the spellings
        learned_path(self.cfg).write_text(json.dumps(learned))
        with self._lock:
            self._done, self._learned = True, learned
        if self.on_learned:
            self.on_learned(learned)
        logger.info(f"enrolled; learned {len(learned)} wake spelling(s)")

    def status(self) -> dict:
        active = self.active
        with self._lock:
            return {"active": active, "heard": len(self._takes or []) if active else (len(LINES) if self._done else 0),
                    "done": self._done, "learned": self._learned, "error": self._error}

    def delete(self):
        self.cancel()
        delete_voiceprint(self.cfg)
        self._vp.clear()
        logger.info("voiceprint deleted: every voice is heard")


def make_enrol_router(enrolment: Enrolment, token: str) -> APIRouter:
    router = APIRouter()

    def check(tok):
        if not tok or not hmac.compare_digest(tok.encode(), token.encode()):
            raise HTTPException(403)

    @router.post("/enrol/start")
    async def start(x_voice_token: str | None = Header(None)):
        check(x_voice_token)
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
