import json
from pathlib import Path

from autonomy import sandbox_settings
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
