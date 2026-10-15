import json

import numpy as np
import pytest

from config import Config
from voiceprint import (Voiceprint, build_vector, delete_voiceprint, learn_spellings, learned_path,
                        learned_wake_phrases, save_voiceprint, speech_only, voiceprint_path)

MU = np.array([0.0, 0.0, 2 ** -0.5])  # the part OWNER and OTHER share, once normalised
OWNER, OTHER = np.array([1.0, 0.0, 1.0]), np.array([0.0, 1.0, 1.0])
LOUD = 0.3


def pcm(secs, who="owner", level=LOUD):
    """secs of 'speech' at level; the sign of the first sample says who it is."""
    x = np.full(int(secs * 16000), level if who == "owner" else -level, dtype=np.float32)
    return (x * 32767).astype(np.int16).tobytes()


def fake_embed(x):
    v = OWNER if x[0] > 0 else OTHER
    return v / np.linalg.norm(v)


def vp(threshold=0.6):
    return Voiceprint(build_vector([fake_embed(np.ones(1))], MU), MU, threshold, fake_embed)


def cfg(tmp_path):
    return Config(data_dir=str(tmp_path), models_dir=str(tmp_path / "models"))


def test_owner_passes_and_others_fail():
    v = vp()
    assert v.is_owner(pcm(1.0)) and not v.is_owner(pcm(1.0, "other"))
    assert v.score(pcm(1.0)) == pytest.approx(1.0)


def test_too_little_speech_is_not_the_owner():
    silence = np.zeros(16000, dtype=np.int16).tobytes()
    assert vp().score(pcm(0.2)) is None and not vp().is_owner(pcm(0.2))
    assert not vp().is_owner(silence + pcm(0.2) + silence)


def test_speech_only_drops_the_silence_around_speech():
    silence = np.zeros(16000, dtype=np.int16).tobytes()
    x = speech_only(silence + pcm(0.5) + silence)
    assert 0.5 <= len(x) / 16000 <= 0.6  # plus 2 frames of padding each side


def test_centring_removes_what_everyone_shares():
    # Without centring OWNER and OTHER share the MU axis (cos 0.5); centred they are orthogonal.
    assert vp(threshold=0.4).score(pcm(1.0, "other")) == pytest.approx(0.0, abs=1e-6)


def test_without_a_voiceprint_everyone_passes(tmp_path):
    v = Voiceprint.load(cfg(tmp_path), embed=fake_embed)
    assert not v.enrolled and v.is_owner(pcm(0.1, "other"))


def test_save_load_is_private_and_round_trips(tmp_path):
    c = cfg(tmp_path)
    save_voiceprint(c, np.array([1.0, 0.0, 0.0]), MU)
    assert voiceprint_path(c).stat().st_mode & 0o777 == 0o600
    v = Voiceprint.load(c, embed=fake_embed)
    assert v.enrolled and np.allclose(v.vector, [1, 0, 0]) and np.allclose(v.mean, MU)


def test_a_voiceprint_from_another_model_is_ignored(tmp_path):
    c = cfg(tmp_path)
    save_voiceprint(c, np.array([1.0, 0.0, 0.0]), MU)
    data = dict(np.load(voiceprint_path(c)))
    np.savez(voiceprint_path(c), **{**data, "model": np.array("old.onnx")})
    assert not Voiceprint.load(c, embed=fake_embed).enrolled


def test_enrolled_but_speaker_model_missing_says_run_install(tmp_path):
    c = cfg(tmp_path)
    save_voiceprint(c, np.array([1.0, 0.0, 0.0]), MU)
    with pytest.raises(RuntimeError, match="install.sh"):
        Voiceprint.load(c)


def test_set_and_clear_switch_the_check_live():
    v = Voiceprint(None, None, 0.6, fake_embed)
    assert v.is_owner(pcm(1.0, "other"))
    v.set(build_vector([fake_embed(np.ones(1))], MU), MU)
    assert not v.is_owner(pcm(1.0, "other"))
    v.clear()
    assert v.is_owner(pcm(1.0, "other"))


def test_delete_removes_new_and_old_files(tmp_path):
    c = cfg(tmp_path)
    save_voiceprint(c, np.array([1.0, 0.0, 0.0]), MU)
    (tmp_path / "voiceprint.npy").write_bytes(b"old")
    learned_path(c).write_text("[]")
    delete_voiceprint(c)
    delete_voiceprint(c)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("said, kept", [
    ("Hey, Mailo.", ["hey mailo"]), ("Hey my luck.", []), ("Hey Milo.", []), ("Hey mo", []),
    ("O K Mylo", ["o k mylo"]), ("Find me a flight", []),
])
def test_learn_spellings(said, kept):
    assert learn_spellings([said], "Milo") == kept


def test_learn_spellings_dedupes():
    assert learn_spellings(["Hey Mailo", "hey, mailo!"], "Milo") == ["hey mailo"]


@pytest.mark.parametrize("content, phrases", [
    ('["hey mailo"]', ["hey mailo"]), ("{not json", []), ('"hey mailo"', []), ('{"hey mailo": 1}', []),
    ("7", []), ('["hey mailo", 3, ""]', ["hey mailo"]),
])
def test_learned_phrases_only_from_a_list_of_strings(tmp_path, content, phrases):
    (tmp_path / "learned_wake.json").write_text(content)
    assert learned_wake_phrases(cfg(tmp_path)) == phrases


def test_embed_is_public_and_none_without_the_model(tmp_path):
    assert Voiceprint.load(cfg(tmp_path)).embed is None
    assert Voiceprint(None, None, 0.6, fake_embed).embed is fake_embed
