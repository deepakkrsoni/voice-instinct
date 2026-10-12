"""Per-run autonomy settings: a sandboxed shell that runs without prompts, secrets walled off.

Claude Code's own sandbox (docs: Sandboxing) confines Bash to the task folder and blocks the
network except approved domains. File tools, WebFetch and MCP run outside it, so the same secret
locations are also denied through permission rules. Deny rules hold in every permission mode.
"""

from pathlib import Path

_SECRET_DIRS = (".ssh", ".aws", ".gnupg", ".config/gh", ".config/gcloud", ".kube", ".docker")
_SECRET_FILES = (".netrc", ".git-credentials", ".npmrc", ".pypirc")


def _fs(p: Path, home: Path) -> str:
    """Sandbox path: '~/x' under home, absolute otherwise."""
    try:
        return "~/" + p.relative_to(home).as_posix()
    except ValueError:
        return p.as_posix()


def _rule(p: Path, home: Path) -> str:
    """Permission-rule path: '~/x' under home; '//abs' for absolute paths (rule syntax)."""
    try:
        return "~/" + p.relative_to(home).as_posix()
    except ValueError:
        return "/" + p.as_posix()


def sandbox_settings(home, app_dir, data_dir) -> dict:
    home, app_dir, data_dir = Path(home), Path(app_dir), Path(data_dir)
    dirs = [home / d for d in _SECRET_DIRS] + [data_dir / "runs"]
    files = [home / f for f in _SECRET_FILES] + [
        app_dir / ".env", data_dir / "hook-token", data_dir / "harness.db"]
    deny = []
    for p in dirs:
        deny += [f"Read({_rule(p, home)}/**)", f"Edit({_rule(p, home)}/**)"]
    for p in files:
        deny += [f"Read({_rule(p, home)})", f"Edit({_rule(p, home)})"]
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,  # never fall back to running unsandboxed
            "autoAllowBashIfSandboxed": True,  # sandboxed shell commands need no prompt
            "allowUnsandboxedCommands": False,  # and cannot be retried outside the sandbox
            "filesystem": {"denyRead": [_fs(p, home) for p in dirs + files]},
        },
        "permissions": {"deny": deny},
    }
