import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from debugbrowser import headless_shell, launch, page_url, pick_target, screenshot

# Playwright MCP's response to a page-changing tool, as Claude Code passes it in tool_response.
RESPONSE = [{"type": "text", "text": "### Ran Playwright code\n```js\nawait page.goto('https://a.example/');\n```\n"
             "### Open tabs\n- 0: [](https://b.example/)\n- 1: (current) [](https://a.example/)\n"
             "### Page\n- Page URL: https://a.example/\n- Page Title: A"}]


def test_page_url_reads_the_current_page_from_the_tool_response():
    assert page_url(RESPONSE) == "https://a.example/"
    assert page_url({"content": RESPONSE}) == "https://a.example/"
    assert page_url("nothing here") is None and page_url(None) is None


def test_the_tab_playwright_reports_is_captured_not_cdps_first_page():
    targets = [{"type": "page", "url": "https://b.example/", "id": "b"},
               {"type": "service_worker", "url": "https://a.example/", "id": "sw"},
               {"type": "page", "url": "https://a.example/", "id": "a"}]
    assert pick_target(targets, "https://a.example/")["id"] == "a"
    assert pick_target(targets, "https://gone.example/")["id"] == "b"  # unknown: first page
    assert pick_target(targets, None)["id"] == "b"
    with pytest.raises(RuntimeError):
        pick_target([{"type": "service_worker", "url": "x"}], None)


def test_headless_shell_finds_the_installers_chromium(tmp_path):
    assert headless_shell(tmp_path) is None
    exe = (tmp_path / ".voice-instinct/browser/browsers/chromium_headless_shell-1247"
           / "chrome-headless-shell-linux64/chrome-headless-shell")
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert headless_shell(tmp_path) == exe


def test_a_browser_that_never_starts_is_reported_and_cleaned_up(tmp_path):
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    with pytest.raises(RuntimeError, match="did not start"):
        asyncio.run(launch(fake, timeout=2))


def test_a_launch_cancelled_while_the_browser_starts_leaves_nothing_running(tmp_path):
    pid_file = tmp_path / "pid"
    slow = tmp_path / "chrome"  # starts but never writes DevToolsActivePort
    slow.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n")
    slow.chmod(0o755)

    async def go():
        t = asyncio.create_task(launch(slow, timeout=20))
        while not pid_file.exists():
            await asyncio.sleep(0.02)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    before = set(Path(tempfile.gettempdir()).glob("vi-browser-*"))
    asyncio.run(go())
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert set(Path(tempfile.gettempdir()).glob("vi-browser-*")) <= before  # its profile is gone too


REAL = headless_shell(Path.home())


@pytest.mark.skipif(REAL is None, reason="the errand browser is not installed (install.sh)")
def test_the_real_browser_starts_empty_screenshots_and_is_removed():
    async def go():
        b = await launch(REAL)
        try:
            assert b.endpoint == f"http://127.0.0.1:{b.port}"
            png = await screenshot(b.port)
        finally:
            await b.close()
        return b, png

    b, png = asyncio.run(go())
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert not Path(b.profile).exists()  # the fresh profile is gone with the browser
