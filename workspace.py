"""Where a delegated task works: the folder the user named, or a private default workspace."""

import re
from pathlib import Path


def _slug(task: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", task.lower())[:4]) or "task"


def resolve_workdir(directory, task, session_id, workspace_root, home) -> Path:
    """Return (and create) the folder a task runs in. Anything outside `home` is refused."""
    home = Path(home).resolve()
    if directory and directory.strip():
        d = directory.strip()
        if d == "~" or d.startswith("~/"):
            p = home / d[2:]
        else:
            p = Path(d)
            if not p.is_absolute():
                p = home / p
    else:
        p = Path(workspace_root) / f"{_slug(task)}-{session_id[:6]}"
    p = p.resolve()  # collapses .. and follows symlinks, so escapes are caught below
    if not p.is_relative_to(home):
        raise ValueError("Pick a folder inside your home directory.")
    p.mkdir(parents=True, exist_ok=True)
    return p
