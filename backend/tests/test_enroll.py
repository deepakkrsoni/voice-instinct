import json

import numpy as np

from config import Config
from enroll import LINES, delete, enroll
from voiceprint import learned_path, voiceprint_path


def test_enrol_saves_a_voiceprint_and_learned_spellings(tmp_path):
    cfg = Config(data_dir=str(tmp_path), name="Milo")
    heard = iter(["Hey Milo.", "Hey Mailo", "Hey my luck"])
    scores = enroll(cfg, record=lambda secs: b"\x01\x00" * 100,
                    embed=lambda pcm, rate=16000: np.array([1.0, 0.0]),
                    transcribe=lambda pcm: next(heard), say=lambda *a: None)
    assert voiceprint_path(cfg).exists()
    assert json.loads(learned_path(cfg).read_text()) == ["hey mailo"]
    assert len(scores) == len(LINES) and min(scores) > 0.99


def test_lines_use_the_configured_name():
    assert sum("Hey {name}" in line for line in LINES) == 3


def test_delete_removes_both_files_and_is_idempotent(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    voiceprint_path(cfg).write_bytes(b"x")
    learned_path(cfg).write_text("[]")
    delete(cfg)
    delete(cfg)
    assert not voiceprint_path(cfg).exists() and not learned_path(cfg).exists()
