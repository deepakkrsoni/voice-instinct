"""Per-run autonomy settings: a sandboxed shell that runs without prompts, secrets walled off.

Claude Code's own sandbox (docs: Sandboxing) confines Bash to the task folder and blocks the
network except approved domains. File tools, WebFetch and MCP run outside it, so the same secret
locations are also denied through permission rules. Deny rules hold in every permission mode.
"""

from pathlib import Path

_SECRET_DIRS = (".ssh", ".aws", ".gnupg", ".config/gh", ".config/gcloud", ".kube", ".docker",
                ".mozilla", ".config/chromium", ".config/google-chrome", ".password-store",
                ".local/share/keyrings",  # logged-in browsers and keyrings
                ".claude")  # Claude's login and the user's own session transcripts (the CLI reads them itself)
_SECRET_FILES = (".netrc", ".git-credentials", ".npmrc", ".pypirc",
                 ".claude.json")  # other MCP servers' secrets
# The errand browser's tools that run code outside the sandbox or read local files into a page.
_BROWSER_DENY = ("mcp__browser__browser_run_code_unsafe", "mcp__browser__browser_file_upload")


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


def sandbox_settings(home, app_dir, data_dir, hook_port=7860) -> dict:
    home, app_dir, data_dir = Path(home), Path(app_dir), Path(data_dir)
    dirs = [home / d for d in _SECRET_DIRS] + [data_dir / "runs"]
    files = [home / f for f in _SECRET_FILES] + [
        app_dir / ".env", data_dir / "hook-token", data_dir / "harness.db",
        data_dir / "harness.db-wal", data_dir / "harness.db-shm"]  # WAL mode keeps recent writes here
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
            # The sandbox proxy refuses loopback unless listed, so without this every hook POST to this
            # app got HTTP 403 and permission questions never reached the user.
            # strictAllowlist: in auto mode a command may otherwise name more hosts for the classifier.
            "network": {"allowedDomains": [f"127.0.0.1:{hook_port}"], "strictAllowlist": True},
        },
        "permissions": {"deny": deny + list(_BROWSER_DENY)},
        # Output past the inline limit is saved under ~/.claude, which the agent cannot read: keep it
        # inline up to the maximum. ponytail: past 128k chars it is still unreadable; give the agent a
        # readable overflow dir if real errands hit that.
        "bashOutputMaxChars": 128000,
        # The user's own instructions are for their own sessions, not for errands.
        "claudeMdExcludes": [str(home / "CLAUDE.md"), str(home / "CLAUDE.local.md")],
    }


def browser_mcp(home):
    """The errand browser for --mcp-config, or None until install.sh has set it up.

    Playwright MCP on the installer's own Node and its own Chromium, headless, with a fresh profile
    each run (--isolated): never signed in to the user's accounts. Page snapshots it saves stay in
    the errand's own folder.
    """
    vi = Path(home) / ".voice-instinct"
    node, cli = vi / "node/bin/node", vi / "browser/node_modules/@playwright/mcp/cli.js"
    browsers = vi / "browser/browsers"
    if not (node.exists() and cli.exists() and browsers.is_dir()):
        return None
    return {"mcpServers": {"browser": {"type": "stdio", "command": str(node),
                                       "args": [str(cli), "--browser", "chromium", "--headless", "--isolated"],
                                       "env": {"PLAYWRIGHT_BROWSERS_PATH": str(browsers)}}}}
