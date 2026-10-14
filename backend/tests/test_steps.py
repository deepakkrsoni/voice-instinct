import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import steps
from hooks import HookBridge, make_router
from registry import Registry
from steps import StepHub, StepRecorder, action

PNG = b"\x89PNG\r\n\x1a\nfake"


def _installed(home):
    vi = home / ".voice-instinct"
    for f in ("node/bin/node", "browser/node_modules/@playwright/mcp/cli.js",
              "browser/browsers/chromium_headless_shell-1/chrome-headless-shell-linux64/chrome-headless-shell"):
        (vi / f).parent.mkdir(parents=True, exist_ok=True)
        (vi / f).touch()


class FakeBrowser:
    port, endpoint, closed = 9, "http://127.0.0.1:9", False

    async def close(self):
        self.closed = True


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_photo(self, png, caption):
        self.sent.append(("photo", caption))

    async def send_message(self, text):
        self.sent.append(("message", text))


def _hub(tmp_path, shoot=None, launch=None, telegram=None):
    _installed(tmp_path)
    browser = FakeBrowser()

    async def fake_launch(exe):
        return browser

    async def fake_shoot(port, url=None):
        return PNG

    hub = StepHub(tmp_path, telegram=telegram, launch=launch or fake_launch, shoot=shoot or fake_shoot)
    return hub, browser


def _step(sid="s1", tool="mcp__browser__browser_click", inp=None, resp=None):
    return {"session_id": sid, "tool_name": tool, "tool_input": inp or {"element": "Search flights", "ref": "e3"},
            "tool_response": resp}


async def _drain(hub):
    await asyncio.gather(*hub._sending)


def test_typed_text_and_form_values_never_reach_the_record():
    assert action("mcp__browser__browser_type", {"element": "Password", "text": "hunter2", "ref": "e9"}) == \
        "browser_type: Password"
    a = action("mcp__browser__browser_fill_form",
               {"fields": [{"name": "Email", "value": "me@x.org"}, {"name": "PIN", "value": "1234"}]})
    assert a == "browser_fill_form: Email PIN" and "1234" not in a
    assert action("mcp__browser__browser_navigate", {"url": "https://a.example/"}) == \
        "browser_navigate: https://a.example/"
    assert action("mcp__browser__browser_navigate_back", {}) == "browser_navigate_back"


def test_a_password_typed_key_by_key_is_not_recorded_but_named_keys_are():
    assert action("mcp__browser__browser_press_key", {"key": "h"}) == "browser_press_key"
    assert action("mcp__browser__browser_press_key", {"key": "Enter"}) == "browser_press_key: Enter"


def test_each_step_is_written_as_it_happens(tmp_path):
    rec = StepRecorder(tmp_path)
    line = rec.record("mcp__browser__browser_click", {"element": "Search"}, PNG)
    assert line.startswith("1. ") and line.endswith("browser_click: Search → 001.png")
    assert (rec.dir / "001.png").read_bytes() == PNG
    line = rec.record("mcp__browser__browser_hover", {"element": "Menu"}, None, "browser gone")
    assert line.endswith("browser_hover: Menu (no screenshot: browser gone)")
    assert not (rec.dir / "002.png").exists()
    assert (rec.dir / "steps.md").read_text().splitlines()[-1] == line  # on disk before the run ends
    report = rec.finish("done")
    assert report.splitlines()[-1].startswith("done: 2 steps in ")
    assert (rec.dir / "steps.md").read_text().splitlines()[-1] == report.splitlines()[-1]


def test_a_resumed_errand_never_overwrites_an_earlier_runs_screenshots(tmp_path):
    first = StepRecorder(tmp_path)
    first.record("mcp__browser__browser_click", {"element": "A"}, PNG)
    second = StepRecorder(tmp_path)  # usually the same second: the -2 suffix case
    second.record("mcp__browser__browser_click", {"element": "B"}, b"other")
    assert first.dir != second.dir and first.dir.parent == second.dir.parent == tmp_path / "debug"
    assert (first.dir / "001.png").read_bytes() == PNG


def test_the_agents_browser_cannot_overwrite_the_record(tmp_path):
    # Playwright MCP runs outside the sandbox and can save snapshots/screenshots under any filename in
    # the errand folder: the record's files must refuse writes from anyone but the recorder.
    rec = StepRecorder(tmp_path)
    rec.record("mcp__browser__browser_click", {"element": "A"}, PNG)
    for name in ("steps.md", "001.png"):
        with pytest.raises(PermissionError):
            (rec.dir / name).write_text("tampered")
    rec.record("mcp__browser__browser_click", {"element": "B"}, PNG)  # the recorder itself still appends
    rec.finish("done")
    assert "browser_click: B" in (rec.dir / "steps.md").read_text()
    with pytest.raises(PermissionError):
        (rec.dir / "steps.md").write_text("tampered")


def test_a_record_that_cannot_be_created_means_an_ordinary_errand(tmp_path):
    hub, _ = _hub(tmp_path)
    (tmp_path / "debug").write_text("a file where the record folder would go")
    assert asyncio.run(hub.start("s1", tmp_path)) is None
    assert asyncio.run(hub.on_step(_step())) == {}


def test_a_step_is_screenshotted_recorded_and_sent(tmp_path):
    tg = FakeTelegram()
    hub, _ = _hub(tmp_path, telegram=tg)

    async def go():
        mcp = json.loads(await hub.start("s1", tmp_path))
        assert mcp["mcpServers"]["browser"]["args"][1:] == ["--cdp-endpoint", "http://127.0.0.1:9"]
        assert await hub.on_step(_step()) == {}
        await _drain(hub)
        return hub._runs["s1"].rec

    rec = asyncio.run(go())
    assert (rec.dir / "001.png").read_bytes() == PNG
    assert tg.sent == [("photo", (rec.dir / "steps.md").read_text().splitlines()[-1])]


def test_the_screenshot_is_of_the_tab_playwright_reports(tmp_path):
    seen = []

    async def shoot(port, url=None):
        seen.append(url)
        return PNG

    hub, _ = _hub(tmp_path, shoot=shoot)
    resp = [{"type": "text", "text": "### Page\n- Page URL: https://a.example/\n"}]

    async def go():
        await hub.start("s1", tmp_path)
        await hub.on_step(_step(resp=resp))

    asyncio.run(go())
    assert seen == ["https://a.example/"]


def test_a_failed_or_hung_screenshot_is_recorded_and_never_blocks_the_errand(tmp_path, monkeypatch):
    monkeypatch.setattr(steps, "SHOT_TIMEOUT", 0.05)
    tg = FakeTelegram()
    calls = iter(["boom", "hang"])

    async def shoot(port, url=None):
        if next(calls) == "boom":
            raise RuntimeError("boom")
        await asyncio.sleep(5)

    hub, _ = _hub(tmp_path, shoot=shoot, telegram=tg)

    async def go():
        await hub.start("s1", tmp_path)
        assert await hub.on_step(_step()) == {}
        assert await hub.on_step(_step()) == {}
        await _drain(hub)
        return hub._runs["s1"].rec

    rec = asyncio.run(go())
    lines = (rec.dir / "steps.md").read_text().splitlines()
    assert lines[-2].endswith("(no screenshot: boom)")
    assert lines[-1].endswith("(no screenshot: TimeoutError)")
    assert [kind for kind, _ in tg.sent] == ["message", "message"]


def test_a_step_from_a_run_without_capture_is_ignored(tmp_path):
    hub, _ = _hub(tmp_path)
    assert asyncio.run(hub.on_step(_step(sid="stale"))) == {}
    assert not (tmp_path / "debug").exists()


def test_no_browser_means_an_ordinary_errand_and_the_record_says_why(tmp_path):
    async def broken(exe):
        raise RuntimeError("the debug browser did not start")

    hub, _ = _hub(tmp_path, launch=broken)

    async def go():
        assert await hub.start("s1", tmp_path) is None  # the backend's normal browser is used
        assert await hub.on_step(_step()) == {}
        return hub._runs["s1"].rec

    rec = asyncio.run(go())
    assert "Debug capture off: the debug browser did not start" in (rec.dir / "steps.md").read_text()
    assert not list(rec.dir.glob("*.png"))


def test_not_installed_means_no_capture(tmp_path):
    hub = StepHub(tmp_path)  # no ~/.voice-instinct here
    assert asyncio.run(hub.start("s1", tmp_path)) is None


def test_finishing_closes_the_browser_and_sends_the_report_last(tmp_path):
    tg = FakeTelegram()
    hub, browser = _hub(tmp_path, telegram=tg)

    async def go():
        await hub.start("s1", tmp_path)
        await hub.on_step(_step())
        await hub.finish("s1", "aborted")
        await hub.finish("s1", "aborted")  # a second finish is harmless
        await _drain(hub)

    asyncio.run(go())
    assert browser.closed and "s1" not in hub._runs
    assert [k for k, _ in tg.sent] == ["photo", "message"]
    assert tg.sent[-1][1].splitlines()[-1].startswith("aborted: 1 step in ")


def test_the_hook_route_hands_post_tool_use_to_the_hub():
    got = []

    async def on_step(p):
        got.append(p)
        return {}

    app = FastAPI()
    app.include_router(make_router(HookBridge(Registry(), None), "tok", steps=SimpleNamespace(on_step=on_step)))
    r = TestClient(app).post("/hook/tok/PostToolUse", json=_step())
    assert r.status_code == 200 and r.json() == {} and got[0]["tool_name"] == "mcp__browser__browser_click"
    plain = FastAPI()
    plain.include_router(make_router(HookBridge(Registry(), None), "tok"))
    assert TestClient(plain).post("/hook/tok/PostToolUse", json=_step()).json() == {}
