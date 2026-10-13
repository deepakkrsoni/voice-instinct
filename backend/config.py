"""Settings for both halves, read from config.toml at the repo root (sectioned; flat keys still work)."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "center")

# config.toml section -> {key in file: Config field}
_SECTIONS = {
    "assistant": {"name": "name", "awake_timeout": "awake_timeout"},
    "model": {"provider": "provider", "model": "model", "api_key_env": "api_key_env", "base_url": "base_url"},
    "gui": {"size": "gui_size", "position": "gui_position"},
    "paths": {"models_dir": "models_dir", "data_dir": "data_dir", "workspace_dir": "workspace_dir",
              "backend_cwd": "backend_cwd"},
    "agent": {"permission_mode": "permission_mode", "backend_timeout": "backend_timeout",
              "permission_timeout": "permission_timeout", "hook_port": "hook_port"},
}


@dataclass
class Config:
    name: str = "Nova"
    awake_timeout: float = 10.0  # seconds of quiet before the orb hides
    provider: str = "gemini"  # gemini | openai | ollama
    model: str = "gemini-3.8-flash"
    base_url: str | None = None  # openai-compatible endpoint; ollama defaults to localhost
    api_key_env: str = "GEMINI_API_KEY"
    gui_size: int = 128
    gui_position: str = "top-right"
    models_dir: str = "~/.voice-instinct/models"
    backend_cwd: str = str(Path.home())
    backend_timeout: float = 300  # must stay well above permission_timeout
    data_dir: str = "~/.voice-instinct"
    workspace_dir: str = "~/.voice-instinct/workspace"  # default folder for tasks that name none
    permission_timeout: float = 90
    hook_port: int = 7860
    permission_mode: str = "auto"  # Claude Code mode for delegated runs; the sandbox bounds what auto may do

    @property
    def wake_phrases(self) -> list[str]:
        words = " ".join(self.name.lower().split())
        return [f"hey {words}", f"okay {words}"]


def load(path=None) -> Config:
    p = Path(path) if path else REPO_ROOT / "config.toml"
    data = tomllib.loads(p.read_text()) if p.exists() else {}
    known = {f.name for f in fields(Config)}
    values = {k: v for k, v in data.items() if k in known}  # legacy flat keys
    for section, mapping in _SECTIONS.items():
        for key, field_name in mapping.items():
            if isinstance(data.get(section), dict) and key in data[section]:
                values[field_name] = data[section][key]
    cfg = Config(**values)
    cfg.name = cfg.name.strip()
    if not cfg.name:
        raise ValueError("config.toml: [assistant] name must not be empty")
    if isinstance(cfg.gui_size, bool) or not isinstance(cfg.gui_size, int) or not 48 <= cfg.gui_size <= 1024:
        raise ValueError("config.toml: [gui] size must be a whole number of pixels from 48 to 1024")
    if isinstance(cfg.awake_timeout, bool) or not isinstance(cfg.awake_timeout, (int, float)) or cfg.awake_timeout <= 0:
        raise ValueError("config.toml: [assistant] awake_timeout must be a number of seconds above 0")
    if cfg.gui_position not in POSITIONS:
        raise ValueError(f"config.toml: [gui] position must be one of {', '.join(POSITIONS)}")
    return cfg
