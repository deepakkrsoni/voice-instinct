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
