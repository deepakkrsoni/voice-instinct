import json
from pathlib import Path

from autonomy import browser_mcp, sandbox_settings, with_debug_record
from hooks import write_run_settings

HOME, APP, DATA = Path("/home/u"), Path("/home/u/voice-agent"), Path("/home/u/.voice-instinct")


def test_sandbox_is_on_fails_closed_and_cannot_be_escaped_by_retry():
    sb = sandbox_settings(HOME, APP, DATA)["sandbox"]
    assert sb["enabled"] is True and sb["failIfUnavailable"] is True  # no silent unsandboxed fallback
    assert sb["autoAllowBashIfSandboxed"] is True  # the autonomy: sandboxed shell runs without prompts
    assert sb["allowUnsandboxedCommands"] is False  # the agent cannot retry a failed command outside the sandbox


def test_secret_locations_are_unreadable_by_both_the_sandbox_and_the_file_tools():
    s = sandbox_settings(HOME, APP, DATA)
    deny_read, rules = s["sandbox"]["filesystem"]["denyRead"], s["permissions"]["deny"]
    for p in ("~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh", "~/.netrc", "~/voice-agent/.env",
              "~/.voice-instinct/hook-token", "~/.voice-instinct/harness.db", "~/.voice-instinct/runs",
              "~/.voice-instinct/harness.db-wal", "~/.voice-instinct/harness.db-shm"):  # WAL mode
        assert p in deny_read, p
    assert "Read(~/.ssh/**)" in rules and "Edit(~/.ssh/**)" in rules
    assert "Read(~/voice-agent/.env)" in rules and "Edit(~/.voice-instinct/hook-token)" in rules


def test_paths_outside_home_use_the_documented_double_slash_for_rules():
    s = sandbox_settings(Path("/home/u"), Path("/opt/app"), Path("/var/vi"))
    assert "/opt/app/.env" in s["sandbox"]["filesystem"]["denyRead"]
    assert "Read(//opt/app/.env)" in s["permissions"]["deny"]


def test_the_workspace_is_not_blocked_even_though_it_lives_under_the_data_dir():
    # ~/.voice-instinct/workspace must stay readable and writable: only the named secrets are denied.
    s = sandbox_settings(HOME, APP, DATA)
    assert "~/.voice-instinct" not in s["sandbox"]["filesystem"]["denyRead"]
    assert not any(r.startswith(("Read(~/.voice-instinct/**", "Edit(~/.voice-instinct/**")) for r in s["permissions"]["deny"])


def test_run_settings_carry_the_sandbox_alongside_the_hooks_and_stay_private(tmp_path):
    import os
    import stat

    path = write_run_settings(tmp_path, "s", "tok", 7860, extra=sandbox_settings(HOME, APP, DATA))
    cfg = json.loads(open(path).read())
    assert "PermissionRequest" in cfg["hooks"] and cfg["sandbox"]["enabled"] is True
    assert cfg["permissions"]["deny"] and stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_sandbox_lets_hooks_reach_this_app_and_nothing_else_local():
    # Claude Code's sandbox proxy refuses loopback unless it is listed: hooks got HTTP 403 without this.
    net = sandbox_settings(HOME, APP, DATA, hook_port=7860)["sandbox"]["network"]
    assert net["allowedDomains"] == ["127.0.0.1:7860"]


def test_errand_browser_is_private_and_only_offered_once_installed(tmp_path):
    from autonomy import browser_mcp

    assert browser_mcp(tmp_path) is None  # not installed: the agent gets no browser at all
    vi = tmp_path / ".voice-instinct"
    (vi / "node/bin").mkdir(parents=True)
    (vi / "node/bin/node").write_text("")
    cli = vi / "browser/node_modules/@playwright/mcp/cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    (vi / "browser/browsers").mkdir()
    server = browser_mcp(tmp_path)["mcpServers"]["browser"]
    assert server["command"] == str(vi / "node/bin/node") and server["args"][0] == str(cli)
    # A fresh profile each run: never signed in to anything (page snapshots stay in the task folder).
    assert "--isolated" in server["args"] and "--headless" in server["args"]
    assert "--user-data-dir" not in server["args"]


def test_the_browsers_code_running_tools_are_denied_in_every_mode():
    # browser_run_code_unsafe runs model-written JS in the MCP server process, outside the sandbox:
    # an agent reading untrusted pages must never get it. Deny rules hold in every permission mode.
    deny = sandbox_settings(HOME, APP, DATA)["permissions"]["deny"]
    assert "mcp__browser__browser_run_code_unsafe" in deny and "mcp__browser__browser_file_upload" in deny


def test_the_shell_cannot_reach_any_host_beyond_the_hook_port():
    # In auto mode a command can name extra hosts for the classifier to approve; strict means it cannot.
    assert sandbox_settings(HOME, APP, DATA)["sandbox"]["network"]["strictAllowlist"] is True


def test_claudes_own_login_browser_profiles_and_keyrings_are_off_limits():
    s = sandbox_settings(HOME, APP, DATA)
    deny_read, rules = s["sandbox"]["filesystem"]["denyRead"], s["permissions"]["deny"]
    for p in ("~/.claude", "~/.claude.json", "~/.mozilla", "~/.config/chromium",
              "~/.config/google-chrome", "~/.password-store", "~/.local/share/keyrings"):
        assert p in deny_read, p
    assert "Read(~/.claude/**)" in rules and "Read(~/.mozilla/**)" in rules


def test_the_users_own_claude_md_is_not_loaded_into_errands():
    assert s_excludes(sandbox_settings(HOME, APP, DATA)) >= {str(HOME / "CLAUDE.md"), str(HOME / "CLAUDE.local.md")}


def s_excludes(s):
    return set(s["claudeMdExcludes"])


def test_errand_browser_is_offered_only_with_its_own_chromium(tmp_path):
    from autonomy import browser_mcp

    vi = tmp_path / ".voice-instinct"
    for f in ("node/bin/node", "browser/node_modules/@playwright/mcp/cli.js"):
        (vi / f).parent.mkdir(parents=True, exist_ok=True)
        (vi / f).write_text("")
    assert browser_mcp(tmp_path) is None  # Chromium missing: no browser rather than one that always fails
    (vi / "browser/browsers").mkdir()
    server = browser_mcp(tmp_path)["mcpServers"]["browser"]
    assert server["env"] == {"PLAYWRIGHT_BROWSERS_PATH": str(vi / "browser/browsers")}  # self-contained


def test_large_shell_output_stays_inline_because_its_overflow_file_is_unreadable():
    # Claude Code saves output past the inline limit under ~/.claude, which the agent cannot read.
    assert sandbox_settings(HOME, APP, DATA)["bashOutputMaxChars"] == 128000


def _install_browser(home):
    vi = home / ".voice-instinct"
    for f in ("node/bin/node", "browser/node_modules/@playwright/mcp/cli.js"):
        (vi / f).parent.mkdir(parents=True, exist_ok=True)
        (vi / f).touch()
    (vi / "browser/browsers").mkdir(parents=True, exist_ok=True)
    return vi / "browser/node_modules/@playwright/mcp/cli.js"


def test_browser_mcp_attaches_to_a_given_cdp_endpoint_instead_of_launching(tmp_path):
    cli = _install_browser(tmp_path)
    assert browser_mcp(tmp_path)["mcpServers"]["browser"]["args"] == [
        str(cli), "--browser", "chromium", "--headless", "--isolated"]
    assert browser_mcp(tmp_path, cdp_endpoint="http://127.0.0.1:9")["mcpServers"]["browser"]["args"] == [
        str(cli), "--cdp-endpoint", "http://127.0.0.1:9"]


def test_debug_runs_deny_edits_to_the_record_without_changing_the_shared_settings():
    base = sandbox_settings(HOME, APP, DATA)
    debug = with_debug_record(base, HOME / "trip", HOME)
    assert "Edit(~/trip/debug/**)" in debug["permissions"]["deny"]
    assert "Edit(~/trip/debug/**)" not in base["permissions"]["deny"]
    assert debug["sandbox"] == base["sandbox"]


def test_errands_may_read_memory_but_never_change_it_nor_read_the_raw_journal():
    s = sandbox_settings(HOME, APP, DATA)
    rules, deny_read = s["permissions"]["deny"], s["sandbox"]["filesystem"]["denyRead"]
    assert "Edit(~/.voice-instinct/memory/**)" in rules  # merged into the sandbox's denyWrite: Bash too
    assert "Read(~/.voice-instinct/memory/**)" not in rules  # grep finds facts there
    assert "Read(~/.voice-instinct/memory/journal/**)" in rules and "~/.voice-instinct/memory/journal" in deny_read


def test_the_nightly_run_writes_memory_but_has_no_network_or_web_and_no_secrets():
    from autonomy import consolidation_settings
    s = consolidation_settings(HOME, APP, DATA)
    rules = s["permissions"]["deny"]
    assert "Edit(~/.voice-instinct/memory/**)" not in rules  # its own folder, journal included
    assert not any("memory/journal" in r for r in rules)
    assert "Read(~/.ssh/**)" in rules and "Read(~/.voice-instinct/harness.db)" in rules
    assert "WebFetch" in rules and "WebSearch" in rules
    assert s["sandbox"]["network"] == {"allowedDomains": [], "strictAllowlist": True}
    assert s["sandbox"]["enabled"] and s["sandbox"]["failIfUnavailable"]
    assert "~/.voice-instinct/memory/journal" not in s["sandbox"]["filesystem"]["denyRead"]


def test_the_nightly_run_cannot_plant_code_that_runs_outside_its_sandbox():
    # Its input holds web text: git hooks, its own next run's settings or a CLAUDE.md would all run or steer
    # code later. .gitignore keeps the raw journal out of git history.
    from autonomy import consolidation_settings
    rules = consolidation_settings(HOME, APP, DATA)["permissions"]["deny"]
    for p in (".git/**", ".claude/**", "CLAUDE.md", ".gitignore"):
        assert f"Edit(~/.voice-instinct/memory/{p})" in rules, p
