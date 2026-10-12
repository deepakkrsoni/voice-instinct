import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from backend import ClaudeCodeBackend
from hooks import HookBridge, make_router, write_run_settings
from registry import Registry

pytestmark = pytest.mark.claude


class AutoRelay:
    def __init__(self, holder, decision):
        self.h, self.decision = holder, decision

    async def raise_question(self, qid):
        asyncio.get_running_loop().call_soon(self.h["bridge"].resolve, qid, self.decision)

    def question_closed(self, qid):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(decision):
    reg, holder = Registry(), {}
    holder["bridge"] = HookBridge(reg, AutoRelay(holder, decision), permission_timeout=30)
    app = FastAPI()
    app.include_router(make_router(holder["bridge"], "tok"))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, reg, port


def run_touch(tmp_path, port, name):
    settings = write_run_settings(tmp_path / "s", "sid", "tok", port)
    b = ClaudeCodeBackend(cwd=str(tmp_path), timeout=120)
    return asyncio.run(b.run(
        f"Create an empty file named {name} using the shell command: touch {name}", settings=settings))


def test_allow_runs_the_command_in_the_same_run(tmp_path):
    server, reg, port = serve("allow")
    try:
        run_touch(tmp_path, port, "yes.txt")
    finally:
        server.should_exit = True
    assert (tmp_path / "yes.txt").exists()
    assert reg.get_question(1)["status"] == "answered"


def test_deny_blocks_the_command(tmp_path):
    server, reg, port = serve("deny")
    try:
        run_touch(tmp_path, port, "no.txt")
    finally:
        server.should_exit = True
    assert not (tmp_path / "no.txt").exists()
    assert reg.get_question(1)["answer"] == "deny"


def test_server_down_fails_open_without_hanging(tmp_path):
    start = time.time()
    r = run_touch(tmp_path, free_port(), "down.txt")  # nothing listens on this port
    assert time.time() - start < 100 and r.text  # returned, did not hang or crash


def run_auto(tmp_path, port, prompt, extra):
    settings = write_run_settings(tmp_path / "s", "sid", "tok", port, extra=extra)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    b = ClaudeCodeBackend(cwd=str(work), timeout=150, permission_mode="auto")
    return work, asyncio.run(b.run(prompt, settings=settings))


def test_autonomy_shell_runs_unprompted_in_the_folder_and_cannot_write_outside(tmp_path):
    from pathlib import Path

    from autonomy import sandbox_settings

    outside = Path.home() / ".vi_outside_test.txt"
    outside.unlink(missing_ok=True)
    server, reg, port = serve("deny")  # if anything asks for permission, it is refused and recorded
    try:
        work, r = run_auto(
            tmp_path, port,
            f"Run these two shell commands one at a time and tell me whether each worked: "
            f"1) echo ok > made.txt   2) touch {outside}",
            sandbox_settings(Path.home(), Path(__file__).parent.parent, tmp_path / "data"))
    finally:
        server.should_exit = True
        made_outside = outside.exists()
        outside.unlink(missing_ok=True)
    assert (work / "made.txt").exists()  # sandboxed shell inside the task folder: autonomous
    assert not made_outside  # the sandbox blocks writes outside it
    assert reg.open_questions() == [] and reg.get_question(1) is None  # and nothing needed a human


def test_secret_deny_rule_stops_the_file_tool_even_in_auto_mode(tmp_path):
    from pathlib import Path

    from autonomy import sandbox_settings

    canary = Path.home() / ".vi_canary_test.txt"
    canary.write_text("CANARY-SECRET-123\n")
    extra = sandbox_settings(Path.home(), Path(__file__).parent.parent, tmp_path / "data")
    extra["permissions"]["deny"].append("Read(~/.vi_canary_test.txt)")
    extra["sandbox"]["filesystem"]["denyRead"].append("~/.vi_canary_test.txt")
    server, reg, port = serve("deny")
    try:
        _, r = run_auto(tmp_path, port, f"Use the Read tool to read {canary} and tell me what it says.", extra)
    finally:
        server.should_exit = True
        canary.unlink(missing_ok=True)
    assert "CANARY-SECRET-123" not in r.text
