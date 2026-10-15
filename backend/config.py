"""Settings for both halves, read from config.toml at the repo root (sectioned; flat keys still work)."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "center")
STT_ENGINES = ("nemotron", "nemotron-multilingual", "parakeet")  # services.make_stt; install.sh's table

# config.toml section -> {key in file: Config field}
_SECTIONS = {
    "assistant": {"name": "name", "awake_timeout": "awake_timeout"},
    "model": {"provider": "provider", "model": "model", "api_key_env": "api_key_env", "base_url": "base_url"},
    "gui": {"size": "gui_size", "position": "gui_position"},
    "paths": {"models_dir": "models_dir", "data_dir": "data_dir", "workspace_dir": "workspace_dir",
              "backend_cwd": "backend_cwd"},
    "agent": {"permission_mode": "permission_mode", "backend_timeout": "backend_timeout",
              "permission_timeout": "permission_timeout"},
    "debug": {"browser_steps": "debug_browser_steps"},
    "stt": {"engine": "stt_engine"},
    "voice": {"threshold": "voice_threshold"},
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
    permission_mode: str = "auto"  # Claude Code mode for delegated runs; the sandbox bounds what auto may do
    debug_browser_steps: bool = False  # screenshot every errand browser step (steps.py)
    stt_engine: str = "nemotron"  # speech-to-text engine (services.make_stt); install.sh downloads only this one
    voice_threshold: float = 0.6  # owner-voice cut-off (voiceprint.py); spike 2 (#32): EER 0 to 5% at 0.60

    @property
    def wake_phrases(self) -> list[str]:
        """The name itself, wherever it is said ("Nova, what's on?", "hey nova"), plus the merged forms speech-to-text
        writes for a quick "Hey Nova" ("Inova", "Anova"). Measured 2026-10-16: Parakeet and Deepgram both heard the
        owner's "Hey Nova" as "hang over" or "Inova", while "Nova" alone came out right (#32)."""
        words = self.name.lower().split()
        merged = [" ".join([p + words[0], *words[1:]]) for p in ("i", "in", "a", "e", "ha", "he", "hey", "o")]
        return [" ".join(words), *merged]

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
    if not isinstance(cfg.name, str) or not cfg.name.strip():
        raise ValueError("config.toml: [assistant] name must be non-empty text")
    cfg.name = cfg.name.strip()
    if isinstance(cfg.gui_size, bool) or not isinstance(cfg.gui_size, int) or not 48 <= cfg.gui_size <= 1024:
        raise ValueError("config.toml: [gui] size must be a whole number of pixels from 48 to 1024")
    if isinstance(cfg.awake_timeout, bool) or not isinstance(cfg.awake_timeout, (int, float)) or cfg.awake_timeout <= 0:
        raise ValueError("config.toml: [assistant] awake_timeout must be a number of seconds above 0")
    if cfg.provider not in ("gemini", "openai", "ollama"):
        raise ValueError("config.toml: [model] provider must be gemini, openai or ollama")
    if not 0 < cfg.permission_timeout < cfg.backend_timeout:
        raise ValueError("config.toml: [agent] permission_timeout must be above 0 and below backend_timeout")
    if cfg.gui_position not in POSITIONS:
        raise ValueError(f"config.toml: [gui] position must be one of {', '.join(POSITIONS)}")
    if not isinstance(cfg.debug_browser_steps, bool):
        raise ValueError("config.toml: [debug] browser_steps must be true or false")
    if cfg.stt_engine not in STT_ENGINES:
        raise ValueError(f"config.toml: [stt] engine must be one of {', '.join(STT_ENGINES)}")
    t = cfg.voice_threshold
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 1:
        raise ValueError("config.toml: [voice] threshold must be a number from 0 to 1")
    return cfg
