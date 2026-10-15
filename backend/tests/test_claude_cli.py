import asyncio
import json
import os
import time
from pathlib import Path

from claude_cli import READ_ONLY_TOOLS, ClaudeCodeBackend, parse_stream

FAKE = str(Path(__file__).parent / "fake_claude.sh")


def test_parse_ignores_noise_and_reads_result():
    lines = [
        '{"type":"system","subtype":"hook_started"}',
        "garbage",
        '{"type":"result","is_error":false,"result":"Four","session_id":"s1","permission_denials":[]}',
    ]
    r = parse_stream(lines)
    assert (r.text, r.session_id, r.denials, r.is_error) == ("Four", "s1", [], False)


def test_parse_denial_shape():
    d = {"tool_name": "Bash", "tool_input": {"command": "touch x"}}
    line = json.dumps({"type": "result", "is_error": False, "result": "blocked",
                       "session_id": "s", "permission_denials": [d]})
    assert parse_stream([line]).denials == [d]


def test_parse_no_result_is_error():
    r = parse_stream(["garbage", '{"type":"system"}'])
    assert r.is_error and r.text == ""


def test_run_uses_cli_and_returns_result():
    r = asyncio.run(ClaudeCodeBackend(cmd=FAKE).run("what is 2+2"))
    assert r.text == "Four" and r.session_id == "sess-1"


def test_run_missing_cli():
    b = ClaudeCodeBackend(cmd="definitely-not-a-real-cli")
    assert not b.available()
    assert asyncio.run(b.run("x")).is_error


def test_run_timeout(tmp_path):
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    r = asyncio.run(ClaudeCodeBackend(cmd=str(slow), timeout=0.3).run("x"))
    assert r.is_error and "timed out" in r.text


def _script(tmp_path, body):
    f = tmp_path / "cli.sh"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)
    return str(f)


def _dead(pid):
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_task_starting_with_dashes_is_passed_as_text(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    r = asyncio.run(ClaudeCodeBackend(cmd=cli).run("--dangerously-skip-permissions"))
    assert r.text.endswith("-- --dangerously-skip-permissions")


def test_find_is_not_read_only():
    assert not any(t.startswith("Bash(find") for t in READ_ONLY_TOOLS)


def test_stdin_is_not_inherited(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$(readlink /proc/self/fd/0)"\n')
    r_fd, w_fd = os.pipe()
    saved = os.dup(0)
    os.dup2(r_fd, 0)
    try:
        r = asyncio.run(ClaudeCodeBackend(cmd=cli).run("x"))
    finally:
        os.dup2(saved, 0)
        os.close(saved), os.close(r_fd), os.close(w_fd)
    assert r.text == "/dev/null"


def test_cancel_kills_the_cli(tmp_path):
    pidfile = tmp_path / "pid"
    cli = _script(tmp_path, f"echo $$ > {pidfile}\nsleep 30\n")

    async def go():
        t = asyncio.create_task(ClaudeCodeBackend(cmd=cli).run("x"))
        await asyncio.sleep(0.5)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert _dead(int(pidfile.read_text()))


def test_cli_runs_as_own_process_group_leader(tmp_path):
    # Needed so timeout/cancel can kill the whole tree (the CLI's own Bash children too).
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s %s"}\\n\' "$$" "$(awk \'{print $5}\' /proc/$$/stat)"\n')
    pid, pgid = asyncio.run(ClaudeCodeBackend(cmd=cli).run("x")).text.split()
    assert pid == pgid


def test_new_session_resume_and_settings_flags(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    b = ClaudeCodeBackend(cmd=cli)
    first = asyncio.run(b.run("t", session_id="abc", settings="/s.json")).text
    again = asyncio.run(b.run("t", session_id="abc", resume=True)).text
    assert "--session-id abc" in first and "--settings /s.json" in first and "--resume" not in first
    assert "--resume abc" in again and "--session-id" not in again


def test_stop_kills_a_running_session_and_run_reports_it(tmp_path):
    cli = _script(tmp_path, "sleep 30\n")

    async def go():
        b = ClaudeCodeBackend(cmd=cli, timeout=60)
        t = asyncio.create_task(b.run("x", session_id="s1"))
        await asyncio.sleep(0.4)
        ok = await b.stop("s1")
        r = await t
        return ok, r, await b.stop("s1")

    ok, r, again = asyncio.run(go())
    assert ok and r.is_error and "stopped" in r.text and again is False


def test_run_uses_the_given_cwd_and_accept_edits_mode(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s|%s"}\\n\' "$(pwd)" "$*"\n')
    work = tmp_path / "work"
    work.mkdir()
    r = asyncio.run(ClaudeCodeBackend(cmd=cli, cwd=str(tmp_path)).run("t", cwd=str(work)))
    cwd, args = r.text.split("|", 1)
    assert cwd == str(work) and "--permission-mode acceptEdits" in args
    plain = asyncio.run(ClaudeCodeBackend(cmd=cli, permission_mode=None).run("t")).text
    assert "--permission-mode" not in plain


def test_the_agent_does_not_inherit_this_apps_model_keys(tmp_path, monkeypatch):
    # The agent reads untrusted web pages; it must not be able to read (and leak) the front model's key.
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-own")  # Claude Code's own login must still work
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "claude-oauth")
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s|%s|%s|%s"}\\n\' "$GEMINI_API_KEY" "$OPENAI_API_KEY" "$ANTHROPIC_API_KEY" "$CLAUDE_CODE_OAUTH_TOKEN"\n')
    assert asyncio.run(ClaudeCodeBackend(cmd=cli).run("x")).text == "||claude-own|claude-oauth"


def test_a_cli_that_fails_without_a_result_says_why(tmp_path):
    # e.g. logged out: the reason is on stderr, and the user should hear it instead of "the task failed".
    cli = _script(tmp_path, 'echo "Invalid API key · Please run /login" >&2\nexit 1\n')
    r = asyncio.run(ClaudeCodeBackend(cmd=cli).run("x"))
    assert r.is_error and "Please run /login" in r.text


def test_the_configured_key_is_hidden_even_without_a_standard_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_GEMINI", "secret")  # [model] api_key_env = "MY_GEMINI"
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"[%s]"}\\n\' "$MY_GEMINI"\n')
    assert asyncio.run(ClaudeCodeBackend(cmd=cli, hide_env=("MY_GEMINI",)).run("x")).text == "[]"


def test_the_agent_gets_none_of_the_users_own_mcp_servers(tmp_path):
    # Without this it inherits every MCP server in ~/.claude.json: other projects' logged-in browsers,
    # Slack, Drive, messaging. An errand agent reading untrusted pages must not reach any of them.
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    assert "--strict-mcp-config" in asyncio.run(ClaudeCodeBackend(cmd=cli).run("t")).text


def test_the_errand_browser_is_the_only_mcp_server_given(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    args = asyncio.run(ClaudeCodeBackend(cmd=cli, mcp_config="/vi/browser-mcp.json").run("t")).text
    assert "--strict-mcp-config" in args and "--mcp-config /vi/browser-mcp.json" in args


def test_the_agent_loads_no_user_level_claude_settings_hooks_or_plugins(tmp_path):
    # The user's ~/.claude/settings.json brings plugins and their SessionStart hooks into every errand.
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    assert "--setting-sources project" in asyncio.run(ClaudeCodeBackend(cmd=cli).run("t")).text


def test_a_run_can_use_its_own_browser_config(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    backend = ClaudeCodeBackend(cmd=cli, mcp_config="/vi/normal.json")
    assert "--mcp-config /vi/debug.json" in asyncio.run(backend.run("t", mcp_config="/vi/debug.json")).text
    assert "--mcp-config /vi/normal.json" in asyncio.run(backend.run("t")).text


def test_errands_are_told_to_keep_research_proportionate(tmp_path):
    # Task 11 opened product page after product page until it was cut off with no answer.
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    args = asyncio.run(ClaudeCodeBackend(cmd=cli).run("t")).text
    assert "--append-system-prompt" in args
    assert "time limit" in args and "listing pages" in args and "Time check" in args


def test_errands_are_told_about_the_user_as_memory_has_it_at_that_moment(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    about = [" Lives in India"]  # the fake CLI echoes args as one JSON line: no newlines
    backend = ClaudeCodeBackend(cmd=cli, about_user=lambda: about[0])
    assert "Time check" in (args := asyncio.run(backend.run("t")).text) and "Lives in India" in args
    about[0] = " Is vegetarian"  # remembered since: read again on every run
    assert "Is vegetarian" in asyncio.run(backend.run("t")).text


def test_another_job_can_bring_its_own_system_prompt(tmp_path):
    cli = _script(tmp_path, 'printf \'{"type":"result","result":"%s"}\\n\' "$*"\n')
    args = asyncio.run(ClaudeCodeBackend(cmd=cli, system_prompt="Consolidate memory.").run("t")).text
    assert "Consolidate memory." in args and "Time check" not in args
