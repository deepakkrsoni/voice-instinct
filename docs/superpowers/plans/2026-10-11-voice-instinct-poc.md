# voice-instinct PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `delegate` tool to the existing voice agent so a fast front LLM hands real tasks to the user's own Claude Code CLI and speaks the result, with a spoken confirmation before anything beyond read-only.

**Architecture:** Existing Pipecat pipeline (Parakeet, Smart Turn v3, front LLM, Kokoro) plus two LLM tools, `delegate` and `confirm_pending`, built by `make_tools(backend)`. `ClaudeCodeBackend` spawns `claude -p --output-format stream-json --verbose` with a read-only tool allowlist; permission denials in the final `result` event are the "needs confirmation" signal. Confirmation is decided by code (`classify` on the actual last user transcript), never by the LLM.

**Tech Stack:** Python 3.13 (uv), pipecat-ai 1.12, pytest, stdlib `tomllib`/`asyncio`.

**Spec:** `docs/superpowers/specs/2026-10-11-voice-instinct-poc-design.md`

## Global Constraints

- Local-only, single user; no servers, accounts, or billing.
- Backend default permission is read-only; write/shell needs spoken "yes".
- Front provider is config (Gemini key, OpenAI-compatible endpoint, or Ollama); default model `gemini-3.8-flash`.
- Backend timeout default 120 s; default working directory is the user's home.
- Commit as the repo's local identity (`deepakkrsoni16@gmail.com`); end commit messages with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Never commit `.env`. Never commit raw CLI output (it can contain hook output); fixtures are hand-trimmed.

## Review Focus

- Hook/noise lines and non-JSON lines in the CLI stream must be ignored, not crash (Task 1 test).
- Stream with no `result` event (CLI crashed) returns an error result, not an exception (Task 1 test).
- `confirm_pending` with nothing pending must not run anything (Task 3 test).
- Mixed replies like "yeah no" or "ok but don't" must be treated as NO (Task 2 test).
- A "yes" said before the confirmation question (stale transcript) must not approve: only the last user message counts, and pending is cleared after any decision except "unclear" (Task 3 test).

---

### Task 1: Claude Code backend adapter

**Files:**
- Create: `backend.py`, `tests/test_backend.py`, `tests/fake_claude.sh`
- Modify: `pyproject.toml` (pytest config + dev dep)

**Interfaces:**
- Produces: `BackendResult(text: str, session_id: str | None, denials: list[dict], is_error: bool)`; `parse_stream(lines: Iterable[str]) -> BackendResult`; `ClaudeCodeBackend(cmd="claude", cwd=None, timeout=120)` with `available() -> bool` and `async run(task: str, session_id: str | None = None, extra_allowed: Iterable[str] = ()) -> BackendResult`; `READ_ONLY_TOOLS: list[str]`.

- [ ] **Step 1: Add pytest**

```bash
cd ~/voice-agent && uv add --dev pytest
cat >> pyproject.toml <<'EOF'

[tool.pytest.ini_options]
pythonpath = ["."]
EOF
```

- [ ] **Step 2: Write the fake CLI and failing tests**

`tests/fake_claude.sh` (prints a hand-trimmed stream: a hook line, a non-JSON line, then a result):

```bash
#!/bin/sh
echo '{"type":"system","subtype":"hook_started","hook_name":"x"}'
echo 'not json at all'
echo '{"type":"result","subtype":"success","is_error":false,"result":"Four","session_id":"sess-1","permission_denials":[]}'
```

`tests/test_backend.py`:

```python
import asyncio
import os
import stat
from pathlib import Path

from backend import BackendResult, ClaudeCodeBackend, parse_stream

FAKE = str(Path(__file__).parent / "fake_claude.sh")
os.chmod(FAKE, os.stat(FAKE).st_mode | stat.S_IXUSR)


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
    r = parse_stream(['{"type":"result","is_error":false,"result":"blocked","session_id":"s","permission_denials":[%s]}' % __import__("json").dumps(d)])
    assert r.denials == [d]


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
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_backend.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'backend'`)

- [ ] **Step 4: Implement**

`backend.py`:

```python
"""Backend adapter: drives the user's own logged-in Claude Code CLI headlessly."""

import asyncio
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field

# ponytail: allowlist by command prefix; widen via spoken confirmation, not config.
READ_ONLY_TOOLS = [
    "Read", "Glob", "Grep",
    "Bash(ls:*)", "Bash(du:*)", "Bash(find:*)", "Bash(stat:*)", "Bash(wc:*)", "Bash(cat:*)",
]


@dataclass
class BackendResult:
    text: str
    session_id: str | None = None
    denials: list[dict] = field(default_factory=list)
    is_error: bool = False


def parse_stream(lines: Iterable[str]) -> BackendResult:
    """Return the final `result` event; hook/system/non-JSON lines are ignored."""
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("type") == "result":
            return BackendResult(
                text=d.get("result", ""),
                session_id=d.get("session_id"),
                denials=d.get("permission_denials", []),
                is_error=bool(d.get("is_error")),
            )
    return BackendResult(text="", is_error=True)


class ClaudeCodeBackend:
    def __init__(self, cmd: str = "claude", cwd: str | None = None, timeout: float = 120):
        self.cmd, self.cwd, self.timeout = cmd, cwd, timeout

    def available(self) -> bool:
        return shutil.which(self.cmd) is not None

    async def run(
        self, task: str, session_id: str | None = None, extra_allowed: Iterable[str] = ()
    ) -> BackendResult:
        if not self.available():
            return BackendResult(text="The Claude CLI is not installed.", is_error=True)
        tools = ",".join([*READ_ONLY_TOOLS, *extra_allowed])
        args = [self.cmd, "-p", task, "--output-format", "stream-json", "--verbose",
                "--allowedTools", tools]
        if session_id:
            args += ["--resume", session_id]
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=self.cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return BackendResult(text="The task timed out.", is_error=True)
        return parse_stream(out.decode(errors="replace").splitlines())
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_backend.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add backend.py tests pyproject.toml uv.lock
git commit -m "feat: Claude Code backend adapter" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Spoken confirmation logic

**Files:**
- Create: `confirm.py`, `tests/test_confirm.py`

**Interfaces:**
- Produces: `classify(reply: str) -> Literal["yes", "no", "unclear"]`; `last_user_text(messages: list[dict]) -> str`.

- [ ] **Step 1: Write failing tests**

```python
from confirm import classify, last_user_text


def test_yes():
    for s in ["yes", "Yeah, go ahead.", "sure", "ok do it"]:
        assert classify(s) == "yes", s


def test_no_wins_over_yes():
    for s in ["no", "No, don't do it", "yeah no", "ok but don't", "cancel that"]:
        assert classify(s) == "no", s


def test_unclear():
    for s in ["hmm maybe", "what would it do?", ""]:
        assert classify(s) == "unclear", s


def test_last_user_text_handles_str_and_parts():
    msgs = [
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": "Allow it?"},
        {"role": "user", "content": [{"type": "text", "text": "not sure"}]},
    ]
    assert last_user_text(msgs) == "not sure"
    assert last_user_text([]) == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_confirm.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'confirm'`)

- [ ] **Step 3: Implement**

```python
"""Decide yes/no/unclear from the user's actual words; the LLM never decides approval."""

import re

_NO = {"no", "nope", "don't", "dont", "stop", "cancel", "never", "nevermind", "negative"}
_YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "approved", "affirmative"}
_YES_PHRASES = ("go ahead", "do it", "please do")


def classify(reply: str) -> str:
    text = re.sub(r"[^a-z' ]", " ", reply.lower())
    words = set(text.split())
    if words & _NO or "never mind" in text:
        return "no"  # any refusal wins: "yeah no" must not approve
    if words & _YES or any(p in text for p in _YES_PHRASES):
        return "yes"
    return "unclear"


def last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_confirm.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add confirm.py tests/test_confirm.py
git commit -m "feat: spoken confirmation classifier" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Delegate tools

**Files:**
- Create: `delegate.py`, `tests/test_delegate.py`

**Interfaces:**
- Consumes: `ClaudeCodeBackend.run`, `BackendResult` (Task 1); `classify`, `last_user_text` (Task 2).
- Produces: `make_tools(backend) -> list` returning `[delegate, confirm_pending]`, both `async def f(params, ...)` Pipecat direct functions. `params` needs `.result_callback` (async) and, for `confirm_pending`, `.context.get_messages()`.

- [ ] **Step 1: Write failing tests**

```python
import asyncio
from types import SimpleNamespace

from backend import BackendResult
from delegate import make_tools

DENIAL = {"tool_name": "Bash", "tool_input": {"command": "touch /tmp/x"}}


class FakeBackend:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def run(self, task, session_id=None, extra_allowed=()):
        self.calls.append((task, session_id, tuple(extra_allowed)))
        return self.results.pop(0)


def make_params(user_text=""):
    out = []

    async def cb(r, **kw):
        out.append(r)

    ctx = SimpleNamespace(get_messages=lambda: [{"role": "user", "content": user_text}])
    return SimpleNamespace(result_callback=cb, context=ctx), out


def run(coro):
    return asyncio.run(coro)


def test_delegate_returns_result_and_keeps_session():
    b = FakeBackend([BackendResult("big.iso", "s1"), BackendResult("done", "s1")])
    delegate, _ = make_tools(b)
    p, out = make_params()
    run(delegate(p, "biggest file?"))
    assert out[-1] == {"status": "done", "result": "big.iso"}
    run(delegate(p, "and the second?"))
    assert b.calls[1][1] == "s1"


def test_denial_requires_confirmation_and_runs_nothing_more():
    b = FakeBackend([BackendResult("blocked", "s1", [DENIAL])])
    delegate, _ = make_tools(b)
    p, out = make_params()
    run(delegate(p, "make a file"))
    assert out[-1]["status"] == "needs_confirmation" and "touch /tmp/x" in out[-1]["action"]
    assert len(b.calls) == 1


def test_confirm_with_nothing_pending_runs_nothing():
    b = FakeBackend([])
    _, confirm = make_tools(b)
    p, out = make_params("yes")
    run(confirm(p))
    assert out[-1]["status"] == "nothing_pending" and b.calls == []


def test_confirm_yes_reruns_with_denied_tool_allowed():
    b = FakeBackend([BackendResult("blocked", "s1", [DENIAL]), BackendResult("created", "s1")])
    delegate, confirm = make_tools(b)
    run(delegate(make_params()[0], "make a file"))
    p, out = make_params("yeah go ahead")
    run(confirm(p))
    assert out[-1] == {"status": "done", "result": "created"}
    assert b.calls[1][2] == ("Bash",)


def test_confirm_no_cancels_and_clears_pending():
    b = FakeBackend([BackendResult("blocked", "s1", [DENIAL])])
    delegate, confirm = make_tools(b)
    run(delegate(make_params()[0], "make a file"))
    p, out = make_params("no don't")
    run(confirm(p))
    assert out[-1]["status"] == "cancelled"
    p2, out2 = make_params("yes")  # stale yes after a no must not resurrect the task
    run(confirm(p2))
    assert out2[-1]["status"] == "nothing_pending" and len(b.calls) == 1


def test_confirm_unclear_keeps_pending():
    b = FakeBackend([BackendResult("blocked", "s1", [DENIAL]), BackendResult("created", "s1")])
    delegate, confirm = make_tools(b)
    run(delegate(make_params()[0], "make a file"))
    p, out = make_params("hmm what would it do")
    run(confirm(p))
    assert out[-1]["status"] == "unclear"
    p2, out2 = make_params("sure")
    run(confirm(p2))
    assert out2[-1]["status"] == "done"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_delegate.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'delegate'`)

- [ ] **Step 3: Implement**

```python
"""LLM tools: hand real work to the backend agent, with code-enforced spoken confirmation."""

from confirm import classify, last_user_text


def _describe(d: dict) -> str:
    inp = d.get("tool_input") or {}
    detail = inp.get("command") or inp.get("file_path") or inp.get("path") or ""
    return f"{d.get('tool_name', 'a tool')}: {detail}".strip(": ")


def make_tools(backend):
    state = {"session": None, "pending": None}  # ponytail: one task at a time, single user

    async def _run(params, task, extra=()):
        res = await backend.run(task, session_id=state["session"], extra_allowed=extra)
        state["session"] = res.session_id or state["session"]
        if res.denials:
            state["pending"] = (task, res.denials)
            await params.result_callback({
                "status": "needs_confirmation",
                "action": "; ".join(_describe(d) for d in res.denials),
                "instruction": "Tell the user in one sentence what it wants to do and ask "
                               "yes or no. After they answer, call confirm_pending.",
            })
            return
        state["pending"] = None
        await params.result_callback(
            {"status": "error" if res.is_error else "done", "result": res.text}
        )

    async def delegate(params, task: str):
        """Hand a real task (files, shell, web, code) to the backend agent and return its answer. Use for anything beyond casual conversation.

        Args:
            task: A complete, self-contained description of what to do.
        """
        await _run(params, task)

    async def confirm_pending(params):
        """Run the action the user was just asked to approve. Call only after the user has answered the confirmation question."""
        if not state["pending"]:
            await params.result_callback({"status": "nothing_pending"})
            return
        verdict = classify(last_user_text(params.context.get_messages()))
        if verdict == "unclear":
            await params.result_callback(
                {"status": "unclear", "instruction": "Ask the user to answer yes or no."}
            )
            return
        task, denials = state["pending"]
        state["pending"] = None
        if verdict == "no":
            await params.result_callback({"status": "cancelled"})
            return
        extra = sorted({d["tool_name"] for d in denials})  # ponytail: allows whole tool, not just that command
        await _run(params, f"The user approved. Retry: {task}", extra)

    return [delegate, confirm_pending]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -v`
Expected: all tests pass (15)

- [ ] **Step 5: Commit**

```bash
git add delegate.py tests/test_delegate.py
git commit -m "feat: delegate and confirm_pending tools" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Config, wiring, README, demo

**Files:**
- Create: `config.py`, `config.toml`, `tests/test_config.py`, `README.md`
- Modify: `bot.py` (LLM construction, tools, prompt)

**Interfaces:**
- Consumes: `make_tools`, `ClaudeCodeBackend`.
- Produces: `Config` dataclass and `load(path) -> Config`.

- [ ] **Step 1: Failing config test**

```python
from config import load


def test_defaults_when_file_missing(tmp_path):
    c = load(tmp_path / "nope.toml")
    assert c.provider == "gemini" and c.model == "gemini-3.8-flash" and c.backend_timeout == 120


def test_overrides(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('provider = "ollama"\nmodel = "qwen3:8b"\n')
    c = load(f)
    assert (c.provider, c.model) == ("ollama", "qwen3:8b")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'config'`)

- [ ] **Step 3: Implement config**

`config.py`:

```python
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    provider: str = "gemini"  # gemini | openai | ollama
    model: str = "gemini-3.8-flash"
    base_url: str | None = None  # openai-compatible endpoint; ollama defaults to localhost
    api_key_env: str = "GEMINI_API_KEY"
    backend_cwd: str = str(Path.home())
    backend_timeout: float = 120


def load(path="config.toml") -> Config:
    p = Path(path)
    data = tomllib.loads(p.read_text()) if p.exists() else {}
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})
```

`config.toml`:

```toml
# Front model (the voice you talk to). The backend agent is the Claude Code CLI you are logged into.
provider = "gemini"        # gemini | openai | ollama
model = "gemini-3.8-flash"
api_key_env = "GEMINI_API_KEY"
# base_url = "http://localhost:11434/v1"   # for ollama / openai-compatible
```

Run: `uv run pytest tests/test_config.py -v` — Expected: 2 passed.

- [ ] **Step 4: Wire bot.py**

```bash
uv add "pipecat-ai[openai]"
```

In `bot.py`: add imports `from backend import ClaudeCodeBackend`, `from config import load`, `from delegate import make_tools`; extend `SYSTEM_PROMPT`; replace the `llm = GoogleLLMService(...)` line and the `LLMContext(...)` line:

```python
SYSTEM_PROMPT = (
    "You are a voice assistant. Your replies are spoken aloud, so use short, "
    "plain sentences with no markdown, lists, or emoji. Keep answers to one to "
    "three sentences unless asked for more. For anything that needs files, the "
    "shell, the web, or code, call delegate(task) and say one short line like "
    "'on it' first. When a result says needs_confirmation, tell the user what it "
    "wants to do and ask yes or no, then call confirm_pending after they answer. "
    "Never claim a task is done unless a tool result says done."
)


def build_llm(cfg):
    key = os.environ.get(cfg.api_key_env, "ollama")
    if cfg.provider == "gemini":
        return GoogleLLMService(api_key=key, model=cfg.model)
    base = cfg.base_url or ("http://localhost:11434/v1" if cfg.provider == "ollama" else None)
    return OpenAILLMService(api_key=key, model=cfg.model, base_url=base)
```

and inside `bot()`:

```python
    cfg = load()
    backend = ClaudeCodeBackend(cwd=cfg.backend_cwd, timeout=cfg.backend_timeout)
    llm = build_llm(cfg)
    tools = []
    if backend.available():
        tools = make_tools(backend)
        for t in tools:
            llm.register_direct_function(t)
    else:
        logger.warning("claude CLI not found; running front-model-only")
    ...
    context = LLMContext([{"role": "system", "content": SYSTEM_PROMPT}], tools=tools)
```

(`from pipecat.services.openai.llm import OpenAILLMService` for the import.)

- [ ] **Step 5: Import smoke test, then restart the bot**

Run: `uv run python -c "import bot" && uv run pytest -q`
Expected: no errors; all tests pass. Then `pkill -f "bot.py"; nohup uv run bot.py > bot.log 2>&1 &` and confirm port 7860 listens.

- [ ] **Step 6: Manual demo (user at the mic)**

Open http://localhost:7860/client. Say: "What's in my downloads folder, and which file is the biggest?" Expected: spoken "on it", then the correct answer. Then say: "Create an empty file called hello.txt in downloads." Expected: it asks aloud for permission; say "yes" and the file appears; repeat and say "no" and nothing is created. Record per-stage latencies from the log.

- [ ] **Step 7: README and commit**

`README.md`: what it is, one-command run (`uv run bot.py`), config table, the "unaffiliated with Instinct" line, the safety model, and a note that subscription-CLI use depends on the vendor's terms.

```bash
git add config.py config.toml tests/test_config.py bot.py README.md pyproject.toml uv.lock
git commit -m "feat: wire delegate tools, config, README" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** two-layer design (Tasks 1, 3, 4); read-only default and spoken confirmation (Tasks 1-3); provider config (Task 4); CLI-missing fallback (Task 4 wiring, Task 1 test); timeout (Task 1); session reuse via `--resume` (Tasks 1, 3); demo criterion (Task 4 step 6). Barge-in cancelling speech but not the task is Pipecat's default behavior for non-cancelling tools and is **not** covered by a test; verify manually in Task 4 step 6 and set `cancel_on_interruption=False` in `register_direct_function` if the delegate is cancelled when you interrupt.
- **Not covered:** progress-line speech while a task runs (spec calls it optional) and a streaming STT.
- **Type consistency:** `BackendResult`, `run(task, session_id, extra_allowed)`, `make_tools -> [delegate, confirm_pending]` match across tasks.
