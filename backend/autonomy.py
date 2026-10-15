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


def sandbox_settings(home, app_dir, data_dir, hook_port=7860, errand=True) -> dict:
    home, app_dir, data_dir = Path(home), Path(app_dir), Path(data_dir)
    # Memory (memory.py): errands grep it but never change it, and never read the raw journal of conversations.
    dirs = [home / d for d in _SECRET_DIRS] + [data_dir / "runs"] + ([data_dir / "memory/journal"] if errand else [])
    files = [home / f for f in _SECRET_FILES] + [
        app_dir / ".env", data_dir / "hook-token", data_dir / "harness.db",
        data_dir / "harness.db-wal", data_dir / "harness.db-shm"]  # WAL mode keeps recent writes here
    deny = [f"Edit({_rule(data_dir / 'memory', home)}/**)"] if errand else []
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


def consolidation_settings(home, app_dir, data_dir) -> dict:
    """The nightly memory run (consolidate.py): it writes memory, journal included, but the journal holds web
    text from errands, so it gets no network, no web tools and the same secret denials as errands."""
    s = sandbox_settings(home, app_dir, data_dir, errand=False)
    s["sandbox"]["network"] = {"allowedDomains": [], "strictAllowlist": True}
    # Nothing it writes may run or steer code later (git hooks, its next run's settings and instructions, at any
    # depth: Claude Code loads people/CLAUDE.md when it reads people/), and .gitignore keeps the raw journal out
    # of git history. The files our code owns are denied too. Edit denies cover Bash (merged into denyWrite).
    memory = _rule(Path(data_dir) / "memory", Path(home))
    s["permissions"]["deny"] += ["WebFetch", "WebSearch", *(f"Edit({memory}/{p})" for p in (
        "**/.git/**", "**/.claude/**", "**/CLAUDE.md", "**/CLAUDE.local.md", ".gitignore", "notes.md",
        ".consolidated", "journal/**"))]
    return s


def browser_mcp(home, cdp_endpoint=None):
    """The errand browser for --mcp-config, or None until install.sh has set it up.

    Playwright MCP on the installer's own Node and its own Chromium, headless, with a fresh profile
    each run (--isolated): never signed in to the user's accounts. Page snapshots it saves stay in
    the errand's own folder. In debug mode (steps.py) it attaches to a browser we launched instead,
    so every step can be screenshotted.
    """
    vi = Path(home) / ".voice-instinct"
    node, cli = vi / "node/bin/node", vi / "browser/node_modules/@playwright/mcp/cli.js"
    browsers = vi / "browser/browsers"
    if not (node.exists() and cli.exists() and browsers.is_dir()):
        return None
    args = (["--cdp-endpoint", cdp_endpoint] if cdp_endpoint
            else ["--browser", "chromium", "--headless", "--isolated"])
    return {"mcpServers": {"browser": {"type": "stdio", "command": str(node), "args": [str(cli), *args],
                                       "env": {"PLAYWRIGHT_BROWSERS_PATH": str(browsers)}}}}


def with_debug_record(settings, cwd, home):
    """Debug runs: the step record in <cwd>/debug is the user's, not the agent's to change. Claude Code
    merges Edit deny rules into the sandbox's denyWrite, so this holds for Bash too."""
    rule = f"Edit({_rule(Path(cwd) / 'debug', Path(home))}/**)"
    return {**settings, "permissions": {**settings["permissions"],
                                        "deny": [*settings["permissions"]["deny"], rule]}}
