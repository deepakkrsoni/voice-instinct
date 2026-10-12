import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    provider: str = "gemini"  # gemini | openai | ollama
    model: str = "gemini-3.8-flash"
    base_url: str | None = None  # openai-compatible endpoint; ollama defaults to localhost
    api_key_env: str = "GEMINI_API_KEY"
    backend_cwd: str = str(Path.home())
    backend_timeout: float = 300  # must stay well above permission_timeout
    data_dir: str = "~/.voice-instinct"
    workspace_dir: str = "~/.voice-instinct/workspace"  # default folder for tasks that name none
    permission_timeout: float = 90
    hook_port: int = 7860
    permission_mode: str = "auto"  # Claude Code mode for delegated runs; the sandbox bounds what auto may do


def load(path="config.toml") -> Config:
    p = Path(path)
    data = tomllib.loads(p.read_text()) if p.exists() else {}
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})
