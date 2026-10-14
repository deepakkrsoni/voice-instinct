"""Debug mode (#17): every page-changing errand browser step is screenshotted, recorded in the errand
folder and sent to Telegram.

A PostToolUse hook (hooks.STEP_MATCHER) drives it and the agent's next step waits for the capture, so
no step can be skipped. The record lives in <errand>/debug/<run>/: steps.md, one line per step as it
happens (#7's action log can build on it), and 001.png, 002.png, ...
"""

import asyncio
import json
import time
from pathlib import Path

from autonomy import browser_mcp
from debugbrowser import headless_shell, launch, page_url, screenshot

SHOT_TIMEOUT = 10  # seconds; the hook itself times out at 15 (hooks.write_run_settings)
# Never typed text or form values: they may be passwords.
_FIELDS = ("element", "url", "key", "startElement", "endElement")


def action(tool_name, tool_input):
    """One line for a step: the tool and what it acted on."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    parts = [str(inp[k]) for k in _FIELDS if inp.get(k)]
    if isinstance(inp.get("fields"), list):  # fill_form: the field names only
        parts += [str(f["name"]) for f in inp["fields"] if isinstance(f, dict) and f.get("name")]
    name = str(tool_name).removeprefix("mcp__browser__")
    detail = " ".join(" ".join(parts).split())[:200]
    return f"{name}: {detail}" if detail else name


class StepRecorder:
    def __init__(self, cwd):
        base = Path(cwd) / "debug"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = base / stamp
        n = 1
        while self.dir.exists():  # two runs in the same second
            n += 1
            self.dir = base / f"{stamp}-{n}"
        self.dir.mkdir(parents=True)
        self.report, self.n, self.lines, self.started = self.dir / "steps.md", 0, [], time.monotonic()
        self._append(f"# Browser steps, {time.strftime('%Y-%m-%d %H:%M')}\n")

    def _append(self, line):
        with self.report.open("a") as f:
            f.write(line + "\n")

    def note(self, text):
        self.lines.append(text)
        self._append(text)

    def record(self, tool_name, tool_input, png=None, error=None):
        self.n += 1
        shot = f"{self.n:03d}.png"
        if png:
            (self.dir / shot).write_bytes(png)
        tail = f"→ {shot}" if png else f"(no screenshot: {error or 'unknown'})"
        line = f"{self.n}. {time.strftime('%H:%M:%S')} {action(tool_name, tool_input)} {tail}"
        self.lines.append(line)
        self._append(line)
        return line

    def finish(self, outcome):
        """Write the footer; return the whole report for Telegram."""
        footer = f"{outcome}: {self.n} step{'' if self.n == 1 else 's'} in {round(time.monotonic() - self.started)} s"
        self._append(f"\n{footer}")
        return "\n".join(["Browser steps", *self.lines, footer])


class _Run:
    def __init__(self, rec, browser):
        self.rec, self.browser = rec, browser


class StepHub:
    """Debug runs by session id. launch/shoot are injectable for tests."""

    def __init__(self, home, telegram=None, launch=launch, shoot=screenshot):
        self.home, self.telegram, self._launch, self._shoot = Path(home), telegram, launch, shoot
        self._runs, self._sending, self._lock = {}, set(), asyncio.Lock()

    async def start(self, session_id, cwd):
        """Open the run's record and its own browser. Returns the --mcp-config JSON, or None to run the
        errand with the normal browser (and no capture)."""
        rec, browser = StepRecorder(cwd), None
        exe = headless_shell(self.home)
        if exe is None or browser_mcp(self.home) is None:
            rec.note("Debug capture off: the errand browser is not installed.")
        else:
            try:
                browser = await self._launch(exe)
            except Exception as e:
                rec.note(f"Debug capture off: {e}")
        self._runs[session_id] = _Run(rec, browser)
        return json.dumps(browser_mcp(self.home, cdp_endpoint=browser.endpoint)) if browser else None

    async def on_step(self, p):
        run = self._runs.get(p.get("session_id"))
        if run is None or run.browser is None:
            return {}
        png = error = None
        try:
            png = await asyncio.wait_for(
                self._shoot(run.browser.port, page_url(p.get("tool_response"))), SHOT_TIMEOUT)
        except Exception as e:  # timeouts too: record the step anyway, never block the errand
            error = str(e) or type(e).__name__
        line = run.rec.record(p.get("tool_name", ""), p.get("tool_input"), png, error)
        if self.telegram:
            self._send(self.telegram.send_photo(png, line) if png else self.telegram.send_message(line))
        return {}

    async def finish(self, session_id, outcome):
        run = self._runs.pop(session_id, None)
        if run is None:
            return
        if run.browser:
            await run.browser.close()
        report = run.rec.finish(outcome)
        if self.telegram:
            self._send(self.telegram.send_message(report))

    def _send(self, coro):
        """In the background, in order (asyncio.Lock is FIFO): Telegram never slows the agent down."""
        async def in_order():
            async with self._lock:
                await coro

        t = asyncio.create_task(in_order())
        self._sending.add(t)
        t.add_done_callback(self._sending.discard)
