"""Claude Code hook receiver: records events and relays permission requests to the user."""

import asyncio
import hmac
import json
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

_EVENTS = ("PermissionRequest", "Stop", "SessionEnd", "SessionStart")


def hook_response(decision):
    if decision == "allow":
        d = {"behavior": "allow"}
    elif decision == "deny":
        d = {"behavior": "deny", "message": "The user declined this action by voice."}
    else:
        return {}  # no decision: Claude falls back to its normal behavior
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


def describe(tool_name, tool_input):
    inp = tool_input if isinstance(tool_input, dict) else {"input": tool_input}
    detail = (inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("url")
              or inp.get("query") or json.dumps(inp, default=str)[:200])
    return f"{tool_name}: {detail}"


class HookBridge:
    def __init__(self, registry, relay, permission_timeout=90):
        self.reg, self.relay, self.timeout = registry, relay, permission_timeout
        self._waiters: dict[int, asyncio.Future] = {}

    def resolve(self, qid, decision):
        fut = self._waiters.get(qid)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    def expire_session(self, session_id):
        """Release hooks still waiting on this session (its run ended or was stopped)."""
        for qid, fut in list(self._waiters.items()):
            q = self.reg.get_question(qid)
            if q and q["session_id"] == session_id and not fut.done():
                fut.set_result(None)

    async def on_permission_request(self, p):
        sid = p["session_id"]
        self.reg.upsert_session(sid, cwd=p.get("cwd"))
        self.reg.add_event(sid, "PermissionRequest", p)
        qid = self.reg.add_question(sid, "permission", describe(p.get("tool_name"), p.get("tool_input")),
                                    p.get("tool_name"), p.get("tool_input"))
        fut = self._waiters.get(qid)
        first = fut is None
        decision = None
        timed_out = False
        try:
            if first:
                fut = self._waiters[qid] = asyncio.get_running_loop().create_future()
                self.reg.set_state(sid, "waiting_input")
                await self.relay.raise_question(qid)
            decision = await asyncio.wait_for(asyncio.shield(fut), self.timeout)
        except asyncio.TimeoutError:
            decision, timed_out = None, True
        finally:  # also runs on cancellation or a relay failure: never leave a zombie question
            if first:  # only the first delivery closes the question
                self._waiters.pop(qid, None)
                self.reg.resolve_question(qid, "answered" if decision else "expired", decision)
                s = self.reg.get_session(sid)
                if s and s["state"] == "waiting_input":  # do not resurrect an ended session
                    self.reg.set_state(sid, "running")
                self.relay.question_closed(qid, expired=timed_out)
                if not fut.done():
                    fut.set_result(decision)
        return hook_response(decision)

    async def on_event(self, event, p):
        sid = p.get("session_id")
        if not sid:
            return {}
        self.reg.upsert_session(sid, cwd=p.get("cwd"))
        self.reg.add_event(sid, event, p)
        if event == "Stop":
            self.reg.set_state(sid, "idle")
        elif event == "SessionEnd":
            self.reg.set_state(sid, "ended", end_reason=p.get("reason"))
        elif event == "SessionStart":
            self.reg.set_state(sid, "running")
        return {}


def make_router(bridge, token):
    router = APIRouter()

    @router.post("/hook/{tok}/{event}")
    async def hook(tok: str, event: str, request: Request):
        if not hmac.compare_digest(tok.encode(), token.encode()):  # str form raises on non-ASCII
            raise HTTPException(status_code=404)
        try:
            payload = await request.json()
        except Exception:
            return {}
        try:
            if event == "PermissionRequest" and payload.get("session_id"):
                return await bridge.on_permission_request(payload)
            return await bridge.on_event(event, payload)
        except Exception as e:  # never let a bridge bug break the user's Claude session
            logger.error(f"hook {event} failed: {e!r}")
            return {}

    return router


def hide_hook_tokens(record):
    """Logging filter for uvicorn's access log: hook URLs carry the secret token."""
    return "/hook/" not in record.getMessage()


def load_token(path):
    p = Path(path)
    if p.exists():
        p.chmod(0o600)  # someone may have loosened it
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    return tok


def write_run_settings(directory, session_id, token, port, hook_timeout=100, extra=None):
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    hooks = {e: [{"hooks": [{"type": "http", "timeout": hook_timeout,
                             "url": f"http://127.0.0.1:{port}/hook/{token}/{e}"}]}] for e in _EVENTS}
    path = d / f"{session_id}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the URL holds the token
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"hooks": hooks, **(extra or {})}))  # extra: sandbox + deny rules
    return str(path)
