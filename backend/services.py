"""Pipecat services: local speech-to-text (Nemotron streaming, or Parakeet; no built-in one exists)
and the front-LLM builder."""

import asyncio
import functools
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame, UserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService, STTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

PARAKEET_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"


def parakeet_dir(cfg) -> Path:
    return Path(cfg.models_dir).expanduser() / PARAKEET_NAME


def kokoro_paths(cfg) -> tuple[Path, Path]:
    d = Path(cfg.models_dir).expanduser() / "kokoro"
    return d / "kokoro-v1.0.onnx", d / "voices-v1.0.bin"


@functools.cache  # one copy for every session: reconnects must not load the model again
def _load_recognizer(model_dir: Path):
    import sherpa_onnx

    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        joiner=str(model_dir / "joiner.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=4,
        model_type="nemo_transducer",
    )


@functools.cache  # one copy for every session; each session gets its own stream
def _load_online_recognizer(model_dir: Path):
    import sherpa_onnx

    def one(pattern):  # file names differ between builds (encoder.int8.onnx, encoder-….int8.onnx)
        (path,) = sorted(model_dir.glob(pattern))
        return str(path)

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=one("encoder*.onnx"), decoder=one("decoder*.onnx"), joiner=one("joiner*.onnx"),
        tokens=str(model_dir / "tokens.txt"), num_threads=4, sample_rate=16000,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,  # nothing said: reset the stream
        rule2_min_trailing_silence=0.8,  # the end of a sentence: finalize it
        rule3_min_utterance_length=20,  # a very long one: finalize anyway
    )


class ParakeetSTTService(SegmentedSTTService):
    """Parakeet TDT 0.6B v3 via sherpa-onnx; transcribes each VAD segment.

    Pipecat has no sherpa-onnx or local Parakeet service (its local STT options are Whisper and
    Moonshine), so this subclasses SegmentedSTTService. `wants_wav_segments = False` is the
    source-documented way to receive raw 16-bit PCM instead of a WAV container.
    """

    # ponytail: offline model, transcribes after VAD stop; swap for a streaming
    # cache-aware model if STT latency dominates.
    wants_wav_segments = False

    def __init__(self, model_dir: Path | None = None, recognizer=None, voiceprint=None, enrolment=None,
                 **kwargs):
        super().__init__(
            sample_rate=16000,
            ttfs_p99_latency=1.3,  # the 0.8 s pause plus decoding
            settings=STTSettings(model="parakeet-tdt-0.6b-v3", language=Language.EN),
            **kwargs,
        )
        self._rec = recognizer or _load_recognizer(model_dir)
        self._cut = None
        self._voiceprint = voiceprint  # #32: only the owner's segments are decoded
        self._enrolment = enrolment  # #32: while enrolling, segments are takes (enrolment.py)

    def can_generate_metrics(self) -> bool:
        return True

    # A VAD stop (0.2 s for Smart Turn) used to end every segment, so speech reached Parakeet a word at
    # a time and was misheard (#19). Now a stop ends the segment only after PAUSE_SECS of silence.
    # These override SegmentedSTTService's private handlers: pinned to Pipecat 1.12 by test_stt_parakeet.
    PAUSE_SECS = 0.8

    async def _handle_user_started_speaking(self, frame):
        await self._cancel_cut()
        await super()._handle_user_started_speaking(frame)

    async def _handle_user_stopped_speaking(self, frame):
        await self._cancel_cut()
        # The base STTService already cleared _user_speaking for this VAD stop, which makes
        # SegmentedSTTService trim the buffer to 1 s while we wait: keep it set until the cut.
        self._user_speaking = True
        self._cut = self.create_task(self._cut_after_pause(frame))

    async def _cut_after_pause(self, frame):
        await asyncio.sleep(self.PAUSE_SECS)
        self._cut = None
        await SegmentedSTTService._handle_user_stopped_speaking(self, frame)

    async def _cancel_cut(self):
        if self._cut is not None:
            await self.cancel_task(self._cut)
            self._cut = None

    # A TV with gaps under PAUSE_SECS would make one ever-growing segment (4 min: 3.7 GB, and no wake
    # until it ended), so a segment is cut at this length whatever, like Nemotron's rule 3.
    MAX_SEGMENT_SECS = 20

    async def process_audio_frame(self, frame, direction):
        await super().process_audio_frame(frame, direction)
        if self._user_speaking and len(self._audio_buffer) >= self.MAX_SEGMENT_SECS * self.sample_rate * 2:
            pausing = self._cut is not None
            await self._cancel_cut()
            await SegmentedSTTService._handle_user_stopped_speaking(self, frame)  # it never reads the frame
            self._user_speaking = not pausing  # still talking: keep collecting the next segment

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        # SegmentedSTTService marks every transcript finalized, which lets Smart Turn end the turn at the
        # next short stop. If the user started talking again while it was decoded, it is not the end of
        # their turn, so it goes out unfinalized (split "Hey Nova. Remind me…" / "…to call mom.").
        if isinstance(frame, TranscriptionFrame) and self._user_speaking:
            await STTService.push_frame(self, frame, direction)
            return
        await super().push_frame(frame, direction)

    async def cancel(self, frame):
        await self._cancel_cut()
        await super().cancel(frame)

    async def stop(self, frame):
        await self._cancel_cut()
        await super().stop(frame)

    def _decode(self, pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, audio)
        self._rec.decode_stream(stream)
        return stream.result.text.strip()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        if self._enrolment is not None and self._enrolment.active:  # #32: a take, not something said to Milo
            text = await asyncio.to_thread(self._decode, audio)
            await asyncio.to_thread(self._enrolment.offer, audio, text)
            return
        # Checked before decoding: someone else's speech is never even transcribed (#32).
        if self._voiceprint is not None and not await asyncio.to_thread(self._voiceprint.is_owner, audio):
            return
        await self.start_processing_metrics()
        text = await asyncio.to_thread(self._decode, audio)
        await self.stop_processing_metrics()
        if text:  # never logged: until the wake phrase this is whatever the room is saying
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())


class NemotronSTTService(STTService):
    """Nemotron Speech Streaming 0.6B via sherpa-onnx: one continuous stream, so the model hears whole
    sentences. Finals come on sherpa's endpoint (0.8 s of trailing silence) or at the end of the
    user's turn, never on the VAD's 0.2 s stops: those chopped speech into misheard pieces (#19)."""

    def __init__(self, model_dir: Path | None = None, recognizer=None, language: Language | None = Language.EN,
                 **kwargs):
        # Smart Turn waits this long for a final after the VAD stop. Measured: sherpa's endpoint fires
        # 0.74-1.34 s after speech ends, so 1.2 split "Hey Nova … <request>" into two turns. A final
        # is marked finalized and ends the turn at once, so the normal path never waits this out.
        super().__init__(sample_rate=16000, ttfs_p99_latency=1.8,
                         settings=STTSettings(model=model_dir.name if model_dir else "nemotron", language=language),
                         **kwargs)
        self._rec = recognizer or _load_online_recognizer(model_dir)
        self._stream = self._rec.create_stream()
        self._partial = ""
        # UserStoppedSpeakingFrame is a SystemFrame: it can arrive while a chunk is being decoded in a
        # thread, so the stream is only touched under this lock.
        self._lock = asyncio.Lock()

    def can_generate_metrics(self) -> bool:
        return True

    def _feed(self, pcm: bytes) -> tuple[str, bool]:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._stream.accept_waveform(16000, samples)
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        return self._rec.get_result(self._stream).strip(), self._rec.is_endpoint(self._stream)

    def _final(self) -> TranscriptionFrame | None:
        text, self._partial = self._partial, ""
        self._rec.reset(self._stream)
        if not text:
            return None
        frame = TranscriptionFrame(text, self._user_id, time_now_iso8601())
        frame.finalized = True  # the end-of-turn strategy releases the turn on it
        return frame

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        out = []
        async with self._lock:
            try:
                text, endpoint = await asyncio.to_thread(self._feed, audio)
            except Exception as e:  # keep listening: one bad chunk must not end the session
                self._partial = ""
                self._rec.reset(self._stream)
                error = e
            else:
                error = None
                if text and text != self._partial:  # never logged: before the wake phrase it is the room
                    self._partial = text
                    out.append(InterimTranscriptionFrame(text, self._user_id, time_now_iso8601()))
                if endpoint and (final := self._final()):
                    out.append(final)
        if error is not None:
            await self.push_error(f"{self}: transcription failed: {error}", exception=error)
        for frame in out:  # yielded outside the lock: downstream must never wait on it
            yield frame

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, UserStoppedSpeakingFrame):
            async with self._lock:
                final = self._final()
            if final:
                await self.push_frame(final)  # the turn ended: what was heard so far is the sentence
        await super().process_frame(frame, direction)


# The official sherpa-onnx int8 builds; install.sh downloads the configured one (same names there).
STT_MODELS = {
    "nemotron": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25",
    "nemotron-multilingual": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-160ms-int8-2026-06-11",
    "parakeet": PARAKEET_NAME,
}


def stt_dir(cfg) -> Path:
    return Path(cfg.models_dir).expanduser() / STT_MODELS[cfg.stt_engine]


def make_stt(cfg, recognizer=None, voiceprint=None, enrolment=None):
    """The configured speech-to-text service. recognizer: a fake for tests. voiceprint: Parakeet only (#32)."""
    d = stt_dir(cfg)
    if recognizer is None and not d.is_dir():
        raise RuntimeError(f"The {cfg.stt_engine} speech model is not installed ({d}). Run ./install.sh.")
    if cfg.stt_engine == "parakeet":
        return ParakeetSTTService(model_dir=d, recognizer=recognizer, voiceprint=voiceprint, enrolment=enrolment)
    return NemotronSTTService(model_dir=d, recognizer=recognizer,
                              language=Language.EN if cfg.stt_engine == "nemotron" else None)


def build_llm(cfg, system_instruction: str):
    """Front LLM. Model and system prompt go through Settings (the documented 1.x way)."""
    key = os.environ.get(cfg.api_key_env, "ollama")
    if cfg.provider == "gemini":
        return GoogleLLMService(
            api_key=key,
            settings=GoogleLLMService.Settings(model=cfg.model, system_instruction=system_instruction),
        )
    base = cfg.base_url or ("http://localhost:11434/v1" if cfg.provider == "ollama" else None)
    return OpenAILLMService(
        api_key=key,
        base_url=base,
        settings=OpenAILLMService.Settings(model=cfg.model, system_instruction=system_instruction),
    )
