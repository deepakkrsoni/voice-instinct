"""Debug mode's errand browser (#17): launched by us, so every step can be screenshotted over CDP.

The installer's headless Chromium with a fresh profile per run, like --isolated: it starts empty and
is never signed in. Playwright MCP attaches to it with --cdp-endpoint (autonomy.browser_mcp).
"""

import asyncio
import base64
import os
import re
import shutil
import signal
import tempfile
from pathlib import Path

import aiohttp


def headless_shell(home):
    """The installer's headless Chromium, or None."""
    found = sorted((Path(home) / ".voice-instinct/browser/browsers").glob(
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))
    return found[-1] if found else None


class Browser:
    def __init__(self, proc, profile):
        self.proc, self.profile, self.port = proc, profile, None

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.port}"

    async def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)  # its renderer and GPU children too
        except ProcessLookupError:
            pass
        await self.proc.wait()
        shutil.rmtree(self.profile, ignore_errors=True)


async def launch(exe, timeout=10):
    profile = tempfile.mkdtemp(prefix="vi-browser-")
    proc = await asyncio.create_subprocess_exec(
        str(exe), f"--user-data-dir={profile}", "--remote-debugging-port=0", "--no-first-run", "about:blank",
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True)
    browser = Browser(proc, profile)
    port_file = Path(profile) / "DevToolsActivePort"  # Chromium writes its chosen port here
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and proc.returncode is None:
        text = port_file.read_text() if port_file.exists() else ""
        if text.strip():
            browser.port = int(text.split()[0])
            return browser
        await asyncio.sleep(0.05)
    await browser.close()
    raise RuntimeError("the debug browser did not start")


def _texts(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _texts(v)
    elif isinstance(x, list):
        for v in x:
            yield from _texts(v)


def page_url(tool_response):
    """The current page Playwright MCP reports after a page-changing tool ('- Page URL: ...')."""
    for text in _texts(tool_response):
        if m := re.search(r"^- Page URL: (.+)$", text, re.M):
            return m.group(1).strip()
    return None


def pick_target(targets, url=None):
    """The CDP page showing `url`. CDP's list does not follow Playwright's tab switches, so its first
    page is only the fallback. ponytail: two tabs on the same URL capture the first of them."""
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("no open page")
    return next((t for t in pages if url and t.get("url") == url), pages[0])


async def screenshot(port, url=None):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/json/list") as r:
            target = pick_target(await r.json(content_type=None), url)
        async with s.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            await ws.send_json({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}})
            async for msg in ws:
                d = msg.json()
                if d.get("id") == 1:
                    if "error" in d:
                        raise RuntimeError(d["error"].get("message", "screenshot failed"))
                    return base64.b64decode(d["result"]["data"])
    raise RuntimeError("the browser closed the connection")
