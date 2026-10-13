import numpy as np

from config import Config
from pipecat.utils.types import NOT_GIVEN
from services import ParakeetSTTService, build_llm


class FakeRecognizer:
    def __init__(self):
        self.seen = []

    def create_stream(self):
        outer = self

        class S:
            result = type("R", (), {"text": "  hello world  "})()

            def accept_waveform(self, sr, audio):
                outer.seen.append((sr, len(audio)))

        return S()

    def decode_stream(self, stream):
        pass


def test_parakeet_declares_model_and_language_so_pipecat_validation_is_clean():
    # Runtime log in the first voice demo: "STTSettings: the following fields are NOT_GIVEN: model, language"
    svc = ParakeetSTTService(recognizer=FakeRecognizer())
    assert svc._settings.model is not NOT_GIVEN and svc._settings.language is not NOT_GIVEN


def test_parakeet_decodes_16k_pcm_through_the_recognizer():
    rec = FakeRecognizer()
    svc = ParakeetSTTService(recognizer=rec)
    pcm = (np.ones(1600, dtype=np.int16) * 1000).tobytes()
    assert svc._decode(pcm) == "hello world" and rec.seen == [(16000, 1600)]


def test_gemini_service_gets_model_and_system_instruction_via_settings(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "k")
    llm = build_llm(Config(provider="gemini", model="gemini-3.8-flash", api_key_env="TEST_KEY"), "BE BRIEF")
    assert llm._settings.model == "gemini-3.8-flash" and llm._settings.system_instruction == "BE BRIEF"


def test_ollama_and_openai_compatible_get_the_same_settings(monkeypatch):
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    cfg = Config(provider="ollama", model="qwen3:8b", api_key_env="NO_SUCH_KEY")
    llm = build_llm(cfg, "BE BRIEF")
    assert llm._settings.model == "qwen3:8b" and llm._settings.system_instruction == "BE BRIEF"


from pathlib import Path

from services import PARAKEET_NAME, kokoro_paths, parakeet_dir


def test_model_paths_come_from_models_dir():
    cfg = Config(models_dir="/models")
    assert parakeet_dir(cfg) == Path("/models") / PARAKEET_NAME
    assert kokoro_paths(cfg) == (Path("/models/kokoro/kokoro-v1.0.onnx"), Path("/models/kokoro/voices-v1.0.bin"))
