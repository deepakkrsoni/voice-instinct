import asyncio
import json
from pathlib import Path

from backend import ClaudeCodeBackend, parse_stream

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
