from config import load


def test_defaults_when_file_missing(tmp_path):
    c = load(tmp_path / "nope.toml")
    assert c.provider == "gemini" and c.model == "gemini-3.8-flash" and c.backend_timeout == 300


def test_overrides(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('provider = "ollama"\nmodel = "qwen3:8b"\n')
    c = load(f)
    assert (c.provider, c.model) == ("ollama", "qwen3:8b")


def test_bridge_defaults():
    c = load("/nonexistent.toml")
    assert (c.permission_timeout, c.hook_port, c.data_dir) == (90, 7860, "~/.voice-instinct")


def test_workspace_default_is_a_dot_folder_in_home():
    assert load("/nonexistent.toml").workspace_dir == "~/.voice-instinct/workspace"


def test_auto_permission_mode_is_the_default():
    assert load("/nonexistent.toml").permission_mode == "auto"


import pytest

from config import POSITIONS, REPO_ROOT, Config


def test_sectioned_file_maps_to_fields(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(
        '[assistant]\nname = "Juno"\nawake_timeout = 12\n'
        '[model]\nprovider = "ollama"\nmodel = "qwen3:8b"\n'
        '[gui]\nsize = 200\nposition = "top-left"\n'
        '[paths]\nmodels_dir = "/m"\n'
        '[agent]\npermission_mode = "acceptEdits"\n'
    )
    c = load(f)
    assert (c.name, c.awake_timeout, c.provider, c.model) == ("Juno", 12, "ollama", "qwen3:8b")
    assert (c.gui_size, c.gui_position, c.models_dir, c.permission_mode) == (200, "top-left", "/m", "acceptEdits")


def test_flat_legacy_file_still_works(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('provider = "gemini"\nmodel = "gemini-3.8-flash"\n')
    assert load(f).model == "gemini-3.8-flash" and load(f).name == "Nova"


def test_wake_phrases_follow_the_name_whatever_its_case_or_spacing():
    assert Config(name=" Nova ").wake_phrases == ["hey nova", "okay nova", "ok nova"]
    assert Config(name="Mister  Bean").wake_phrases == ["hey mister bean", "okay mister bean", "ok mister bean"]


def test_bad_position_or_empty_name_fails_fast_with_a_clear_message(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[gui]\nposition = "somewhere"\n')
    with pytest.raises(ValueError, match="position"):
        load(f)
    f.write_text('[assistant]\nname = "  "\n')
    with pytest.raises(ValueError, match="name"):
        load(f)


def test_default_config_path_is_the_repo_root():
    assert (REPO_ROOT / "config.example.toml").exists()
    assert "bottom-right" in POSITIONS


@pytest.mark.parametrize("body", ['[gui]\nsize = "big"', "[gui]\nsize = 10", '[assistant]\nawake_timeout = "10"', "[assistant]\nawake_timeout = 0"])
def test_a_size_or_timeout_that_is_not_a_sensible_number_fails_fast(tmp_path, body):
    f = tmp_path / "c.toml"
    f.write_text(body)
    with pytest.raises(ValueError, match="config.toml"):
        load(f)


def test_a_name_that_is_not_text_fails_fast_with_a_clear_message(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("[assistant]\nname = 7\n")
    with pytest.raises(ValueError, match="name"):
        load(f)


def test_permission_timeout_must_be_shorter_than_the_backend_timeout(tmp_path):
    # Otherwise the whole run is killed while the user is still being asked.
    f = tmp_path / "c.toml"
    f.write_text("[agent]\nbackend_timeout = 60\npermission_timeout = 90\n")
    with pytest.raises(ValueError, match="permission_timeout"):
        load(f)


def test_an_unknown_provider_fails_fast(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[model]\nprovider = "gemeni"\n')
    with pytest.raises(ValueError, match="provider"):
        load(f)


def test_ok_spelled_the_way_speech_to_text_writes_it_also_wakes_it():
    assert "ok nova" in Config().wake_phrases
