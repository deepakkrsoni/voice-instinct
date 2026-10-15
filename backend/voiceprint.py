"""The owner's voice (#32): a TitaNet-small speaker embedding, made by enrolling through the assistant itself
(enrolment.py) and checked on the wake phrase while it sleeps, as Siri does. No voiceprint
file means the check is off.

Spike 2 (orb audio, 26 owner lines, 25 phone segments): EER 0 to 5%, cut-off 0.60. Speech only, because a
segment is mostly silence; cohort-centred, because short clips share a lot whoever speaks."""

import functools
import json
import os
import re
import zipfile
from pathlib import Path

import numpy as np
from loguru import logger

SPEAKER_MODEL = "nemo_en_titanet_small.onnx"
MIN_SPEECH_SECS = 0.25  # under this, a cough or a click: never judged as a voice
COHORT_VOICES = ("am_adam", "am_michael", "bf_emma", "af_bella", "bm_george", "af_nicole", "am_echo",
                 "bm_lewis", "hf_alpha", "hm_omega", "hf_beta", "hm_psi")  # Kokoro; spike 2's cohort
WAKE_PREFIXES = ("hey", "hay", "okay", "ok", "o k")  # as Config.wake_phrases, plus Parakeet's "O K"
FRAME = 320  # 20 ms
SPEECH_RELATIVE = 0.1  # -20 dB from the loudest frame; at the owner's level (~0.15) the same as spike 2's fixed 0.02
SPEECH_FLOOR = 0.003  # a clip that never gets louder than this has no speech in it


def voiceprint_path(cfg) -> Path:
    return Path(cfg.data_dir).expanduser() / "voiceprint.npz"


def learned_path(cfg) -> Path:
    return Path(cfg.data_dir).expanduser() / "learned_wake.json"


def speaker_model_path(cfg) -> Path:
    return Path(cfg.models_dir).expanduser() / SPEAKER_MODEL


def speech_only(pcm: bytes) -> np.ndarray:
    """The 20 ms frames within 20 dB of the clip's loudest, plus 2 each side. Relative, so a quiet mic or a
    soft voice works; the floor drops near silence (the orb's noise-suppressed silence is ~0.0005)."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(x) // FRAME
    f = x[: n * FRAME].reshape(n, FRAME)
    rms = np.sqrt((f**2).mean(1))
    if not n or rms.max() < SPEECH_FLOOR:
        return np.zeros(0, dtype=np.float32)
    keep = np.convolve(rms > rms.max() * SPEECH_RELATIVE, np.ones(5), "same") > 0
    return f[keep].ravel()


@functools.cache  # one copy for every session, like the recognisers
def _extractor(model_path: Path):
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=2))


def embed_with(model_path: Path):
    def embed(samples: np.ndarray) -> np.ndarray:
        ex = _extractor(model_path)
        s = ex.create_stream()
        s.accept_waveform(16000, samples)
        s.input_finished()
        v = np.array(ex.compute(s))
        return v / np.linalg.norm(v)
    return embed


def cohort_mean(embed, kokoro_paths) -> np.ndarray:
    """The mean embedding of COHORT_VOICES saying one line: what any voice shares, removed before scoring."""
    import soxr
    from kokoro_onnx import Kokoro

    k = Kokoro(*map(str, kokoro_paths))
    out = []
    for voice in COHORT_VOICES:
        a, sr = k.create("Book a table for two tonight and remind me later.", voice=voice)
        out.append(embed(soxr.resample(a, sr, 16000).astype(np.float32)))
    return np.mean(out, axis=0)


def _centre(e, mean):
    c = e - mean
    return c / np.linalg.norm(c)


def build_vector(embeddings, mean) -> np.ndarray:
    v = np.mean([_centre(e, mean) for e in embeddings], axis=0)
    return v / np.linalg.norm(v)


class Voiceprint:
    def __init__(self, vector=None, mean=None, threshold: float = 0.6, embed=None):
        self.vector, self.mean, self.threshold, self._embed = vector, mean, threshold, embed

    @property
    def enrolled(self) -> bool:
        return self.vector is not None

    @property
    def embed(self):
        """The embedder, or None when the speaker model isn't installed."""
        return self._embed

    def set(self, vector, mean) -> None:
        self.vector, self.mean = vector, mean

    def clear(self) -> None:
        self.vector = self.mean = None

    @classmethod
    def load(cls, cfg, embed=None) -> "Voiceprint":
        path = voiceprint_path(cfg)
        model = speaker_model_path(cfg)
        if embed is None and model.exists():
            embed = embed_with(model)
        if not path.exists():
            logger.info("voice check off: run `uv run enroll.py` so only your voice can wake it")
            return cls(None, None, cfg.voice_threshold, embed)
        try:
            data = dict(np.load(path))
            model_name = str(data["model"])
        except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile) as e:
            logger.warning(f"voice check off: {path} is damaged ({type(e).__name__}); run enroll.py again")
            return cls(None, None, cfg.voice_threshold, embed)
        if model_name != SPEAKER_MODEL:
            logger.warning(f"voice check off: {path} was made with another model; run enroll.py again")
            return cls(None, None, cfg.voice_threshold, embed)
        if embed is None:
            raise RuntimeError(f"The speaker model is not installed ({model}). Run ./install.sh.")
        return cls(data["vector"], data["mean"], cfg.voice_threshold, embed)

    def score(self, pcm: bytes) -> float | None:
        """Similarity to the owner, or None when there is too little speech to judge."""
        x = speech_only(pcm)
        if len(x) < MIN_SPEECH_SECS * 16000:
            return None
        return float(_centre(self._embed(x), self.mean) @ self.vector)

    def is_owner(self, pcm: bytes) -> bool:
        if not self.enrolled:
            return True
        s = self.score(pcm)
        logger.info(f"voice check {s if s is None else round(s, 2)} (cut-off {self.threshold}): {'owner' if s is not None and s >= self.threshold else 'not the owner'}")
        return s is not None and s >= self.threshold


def save_voiceprint(cfg, vector, mean) -> None:
    path = voiceprint_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        np.savez(f, vector=vector, mean=mean, model=np.array(SPEAKER_MODEL))
    os.chmod(path, 0o600)  # an existing file keeps its old mode otherwise


def delete_voiceprint(cfg) -> None:
    for p in (voiceprint_path(cfg), voiceprint_path(cfg).with_suffix(".npy"), learned_path(cfg)):
        p.unlink(missing_ok=True)  # .npy: revision 1's CAM++ voiceprint


_SPELLING = re.compile(r"^(okay|ok|o k|hey|hay)[\s,]+([a-z]+)\s*(?:[,.!?]|$)")


def learn_spellings(transcripts, name: str) -> list[str]:
    """How Parakeet wrote the owner's wake phrase, kept only as a prefix plus exactly one word before the
    request ("Hey Mailo, what time is it?" gives "hey mailo"). Two words ("hey my luck, ...") are where
    ordinary sentences live."""
    # ponytail: one-word rule only, a real word could slip in ("hey mile"); enroll.py prints what it learned.
    name = " ".join(name.lower().split())
    out = []
    for t in transcripts:
        m = _SPELLING.match(t.lower().strip())
        if m and m.group(2) != name and len(m.group(2)) >= 3 and f"{m.group(1)} {m.group(2)}" not in out:
            out.append(f"{m.group(1)} {m.group(2)}")
    return out


def learned_wake_phrases(cfg) -> list[str]:
    path = learned_path(cfg)
    if not path.exists():
        return []
    try:
        phrases = json.loads(path.read_text())
        if not isinstance(phrases, list):
            raise TypeError("not a list")
        return [p for p in phrases if isinstance(p, str) and p.strip()]
    except (ValueError, TypeError):
        logger.warning(f"{path} is not a JSON list of phrases; ignoring it (run enroll.py again)")
        return []
