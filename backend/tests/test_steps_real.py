"""Debug mode end to end: the real Playwright MCP attached to our browser, the hub capturing each step,
including after a tab switch (CDP's page order does not follow Playwright's)."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from autonomy import browser_mcp
from debugbrowser import headless_shell
from steps import StepHub

HOME = Path.home()
pytestmark = pytest.mark.skipif(headless_shell(HOME) is None or browser_mcp(HOME) is None,
                                reason="the errand browser is not installed (install.sh)")


async def _mcp(config):
    server = json.loads(config)["mcpServers"]["browser"]
    proc = await asyncio.create_subprocess_exec(
        server["command"], *server["args"], stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env={**os.environ, **server["env"]})
    ids = iter(range(1, 100))

    async def call(method, params):
        i = next(ids)
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n").encode())
        await proc.stdin.drain()
        while True:
            d = json.loads(await asyncio.wait_for(proc.stdout.readline(), 30))
            if d.get("id") == i:
                return d["result"]

    await call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "0"}})
    proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    return proc, call


def test_every_step_of_a_real_browser_session_is_captured_from_the_right_tab(tmp_path):
    hub = StepHub(HOME)

    async def go():
        config = await hub.start("s1", tmp_path)
        assert config is not None
        proc, call = await _mcp(config)
        try:
            async def step(tool, args):
                result = await call("tools/call", {"name": tool, "arguments": args})
                await hub.on_step({"session_id": "s1", "tool_name": f"mcp__browser__{tool}",
                                   "tool_input": args, "tool_response": result["content"]})

            await step("browser_navigate", {"url": "data:text/html,<h1>one</h1>"})
            await call("tools/call", {"name": "browser_tabs", "arguments": {"action": "new"}})
            await step("browser_navigate", {"url": "data:text/html,<h1>two</h1>"})
            await call("tools/call", {"name": "browser_tabs", "arguments": {"action": "select", "index": 0}})
            await step("browser_navigate", {"url": "data:text/html,<h1>three</h1>"})
        finally:
            proc.kill()
            await proc.wait()
        rec = hub._runs["s1"].rec
        await hub.finish("s1", "done")
        return rec

    rec = asyncio.run(go())
    shots = sorted(rec.dir.glob("*.png"))
    assert [p.name for p in shots] == ["001.png", "002.png", "003.png"]
    assert all(p.read_bytes().startswith(b"\x89PNG") for p in shots)
    # Step 3 navigated tab 0 after the switch: its screenshot must differ from step 2's (tab 1).
    assert shots[2].read_bytes() != shots[1].read_bytes()
    assert (rec.dir / "steps.md").read_text().splitlines()[-1].startswith("done: 3 steps in ")
