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


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class ClaudeCodeBackend:
    def __init__(self, cmd: str = "claude", cwd: str | None = None, timeout: float = 120):
        self.cmd, self.cwd, self.timeout = cmd, cwd, timeout
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
    ) -> BackendResult:
        if not self.available():
            return BackendResult(text="The Claude CLI is not installed.", is_error=True)
        tools = ",".join([*READ_ONLY_TOOLS, *extra_allowed])
        args = [self.cmd, "-p", "--output-format", "stream-json", "--verbose",
                "--allowedTools", tools]
        if session_id:
            args += ["--resume" if resume else "--session-id", session_id]
        if settings:
            args += ["--settings", settings]
        args += ["--", task]  # "--": a task starting with dashes must stay text, never a flag
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=self.cwd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # own process group so we can kill its Bash children too
        )
        if session_id:
            self._procs[session_id] = proc
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self.timeout)
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
            return BackendResult(text="The task was stopped.", is_error=True)
        return parse_stream(out.decode(errors="replace").splitlines())
