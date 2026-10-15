import json

import numpy as np
import pytest

from config import Config
from voiceprint import Voiceprint, learn_spellings, learned_wake_phrases, save_voiceprint, voiceprint_path

OWNER, OTHER = np.array([1.0, 0.0]), np.array([0.0, 1.0])


def fake_embed(pcm, rate=16000):
    """The first byte says whose voice it is: 1 = owner, anything else = someone else."""
    return OWNER if pcm[:1] == b"\x01" else OTHER


def cfg(tmp_path):
    return Config(data_dir=str(tmp_path), models_dir=str(tmp_path / "models"))


def test_owner_passes_and_others_fail():
    vp = Voiceprint(OWNER, 0.35, fake_embed)
    assert vp.enrolled
    assert vp.is_owner(b"\x01\x00") and not vp.is_owner(b"\x02\x00")
    assert vp.score(b"\x01\x00") == pytest.approx(1.0)


def test_without_a_voiceprint_everyone_passes(tmp_path):
    vp = Voiceprint.load(cfg(tmp_path), embed=fake_embed)
    assert not vp.enrolled and vp.is_owner(b"\x02\x00")


def test_saved_voiceprint_is_the_normalised_mean_and_private(tmp_path):
    c = cfg(tmp_path)
    save_voiceprint(c, [np.array([2.0, 0.0]), np.array([0.0, 2.0])])
    assert voiceprint_path(c).stat().st_mode & 0o777 == 0o600
    vp = Voiceprint.load(c, embed=fake_embed)
    assert np.allclose(vp.vector, [2 ** -0.5, 2 ** -0.5])


def test_enrolled_but_speaker_model_missing_says_run_install(tmp_path):
    c = cfg(tmp_path)
    save_voiceprint(c, [OWNER])
    with pytest.raises(RuntimeError, match="install.sh"):
        Voiceprint.load(c)  # no embed given: it needs the real model file


@pytest.mark.parametrize("said, kept", [
    ("Hey, Mailo.", ["hey mailo"]),
    ("Hey my luck.", []),        # two words: where ordinary speech lives
    ("Hey Milo.", []),           # already a wake phrase
    ("Hey mo", []),              # under 3 letters
    ("O K Mylo", ["o k mylo"]),
    ("Find me a flight", []),    # not a wake prefix
])
def test_learn_spellings(said, kept):
    assert learn_spellings([said], "Milo") == kept


def test_learn_spellings_dedupes():
    assert learn_spellings(["Hey Mailo", "hey, mailo!"], "Milo") == ["hey mailo"]


def test_learned_phrases_load_and_bad_json_is_ignored(tmp_path):
    c = cfg(tmp_path)
    assert learned_wake_phrases(c) == []
    (tmp_path / "learned_wake.json").write_text(json.dumps(["hey mailo"]))
    assert learned_wake_phrases(c) == ["hey mailo"]
    (tmp_path / "learned_wake.json").write_text("{not json")
    assert learned_wake_phrases(c) == []
