"""Backend adapter: drives the user's own logged-in Claude Code CLI headlessly."""

import asyncio
import json
import os
import shutil
import signal
from collections.abc import Iterable
from dataclasses import dataclass, field

# ponytail: allowlist by command prefix; widen via spoken confirmation, not config.
# No `find`: it can delete (-delete/-exec); Glob covers searching.
READ_ONLY_TOOLS = [
    "Read", "Glob", "Grep",
    "Bash(ls:*)", "Bash(du:*)", "Bash(stat:*)", "Bash(wc:*)", "Bash(cat:*)",
]

# Appended to every errand's system prompt. Task 11 opened product page after product page until the
# time limit cut it off with no answer; budget.py sends the "Time check" this refers to.
ERRAND_GUIDANCE = (
    "This errand runs against a time limit you cannot see. Keep research proportionate: prefer search "
    "results and listing pages over opening many product pages one by one. If a Time check message says "
    "time is nearly up, stop and give your best answer at once, saying what you could not check."
)


@dataclass
class BackendResult:
    text: str
    session_id: str | None = None
    denials: list[dict] = field(default_factory=list)
    is_error: bool = False
    stopped: bool = False  # the user stopped it (stop_session): never resume this session


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


def _agent_env(hide=()) -> dict:
    """Our environment minus other services' API keys (e.g. the front model's GEMINI_API_KEY from .env):
    the agent reads untrusted pages and must not be able to leak them. Its own ANTHROPIC_*/CLAUDE_* stay."""
    return {k: v for k, v in os.environ.items()
            if k not in hide and (not (k.endswith("_API_KEY") or k.endswith("_TOKEN"))
                                  or k.startswith(("ANTHROPIC_", "CLAUDE_")))}


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class ClaudeCodeBackend:
    def __init__(self, cmd: str = "claude", cwd: str | None = None, timeout: float = 120,
                 permission_mode: str | None = "acceptEdits", hide_env: Iterable[str] = (),
                 mcp_config: str | None = None, system_prompt: str = ERRAND_GUIDANCE, about_user=None):
        # acceptEdits: file edits inside the task's own folder don't prompt; shell commands still do.
        self.cmd, self.cwd, self.timeout, self.permission_mode = cmd, cwd, timeout, permission_mode
        self.system_prompt = system_prompt
        self.about_user = about_user  # memory.prompt_block: the profile, read fresh for every run (#25)
        self.hide_env = tuple(hide_env)  # e.g. the front model's api_key_env, whatever it is called
        self.mcp_config = mcp_config  # the only MCP servers the agent gets (the errand browser)
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._stopped: set[str] = set()

    def available(self) -> bool:
        return shutil.which(self.cmd) is not None

    async def stop(self, session_id: str) -> bool:
        proc = self._procs.get(session_id)
        if proc is None or proc.returncode is not None:
            return False
        self._stopped.add(session_id)
        _kill_group(proc)
        return True

    async def run(
        self,
        task: str,
        session_id: str | None = None,
        resume: bool = False,
        settings: str | None = None,
        extra_allowed: Iterable[str] = (),
        cwd: str | None = None,
        mcp_config: str | None = None,
    ) -> BackendResult:
        if not self.available():
            return BackendResult(text="The Claude CLI is not installed.", is_error=True)
        tools = ",".join([*READ_ONLY_TOOLS, *extra_allowed])
        args = [self.cmd, "-p", "--output-format", "stream-json", "--verbose",
                "--allowedTools", tools,
                "--append-system-prompt", self.system_prompt + (self.about_user() if self.about_user else ""),
                "--strict-mcp-config",  # none of the user's own MCP servers (their browsers, Slack, mail)
                "--setting-sources", "project"]  # nor their ~/.claude settings, plugins or hooks
        mcp = mcp_config or self.mcp_config  # a debug run brings its own browser (steps.py)
        if mcp:
            args += ["--mcp-config", mcp]
        if session_id:
            args += ["--resume" if resume else "--session-id", session_id]
        if settings:
            args += ["--settings", settings]
        if self.permission_mode:
            args += ["--permission-mode", self.permission_mode]
        args += ["--", task]  # "--": a task starting with dashes must stay text, never a flag
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd or self.cwd, stdin=asyncio.subprocess.DEVNULL, env=_agent_env(self.hide_env),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group so we can kill its Bash children too
        )
        if session_id:
            self._procs[session_id] = proc
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            return BackendResult(text="The task timed out.", is_error=True)
        finally:  # timeout or cancellation (barge-in): never leave the CLI running unattended
            if proc.returncode is None:
                _kill_group(proc)
                await proc.wait()
            if session_id:
                self._procs.pop(session_id, None)
        if session_id in self._stopped:
            self._stopped.discard(session_id)
            return BackendResult(text="The task was stopped.", is_error=True, stopped=True)
        res = parse_stream(out.decode(errors="replace").splitlines())
        if res.is_error and not res.text:  # no result event: the CLI's last stderr line says why
            lines = err.decode(errors="replace").strip().splitlines()
            res.text = f"The Claude CLI failed: {lines[-1][:200]}" if lines else "The Claude CLI failed."
        return res
