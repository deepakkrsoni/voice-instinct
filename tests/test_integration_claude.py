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
