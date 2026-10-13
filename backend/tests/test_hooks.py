import asyncio
import json
import stat

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hooks import HookBridge, hook_response, load_token, make_router, write_run_settings
from registry import Registry


class FakeRelay:
    def __init__(self):
        self.raised, self.closed = [], []

    async def raise_question(self, qid):
        self.raised.append(qid)

    def question_closed(self, qid, expired=False):
        self.closed.append(qid)
        if expired:
            self.expired = getattr(self, "expired", []) + [qid]


PAYLOAD = {"session_id": "s1", "cwd": "/w", "hook_event_name": "PermissionRequest",
           "tool_name": "Bash", "tool_input": {"command": "rm -r build"}}


def make(timeout=5):
    reg, relay = Registry(), FakeRelay()
    return HookBridge(reg, relay, permission_timeout=timeout), reg, relay


def test_permission_blocks_until_resolved_then_allows():
    async def go():
        b, reg, relay = make()
        t = asyncio.create_task(b.on_permission_request(PAYLOAD))
        await asyncio.sleep(0)
        q = reg.open_questions()[0]
        assert relay.raised == [q["id"]] and reg.get_session("s1")["state"] == "waiting_input"
        assert b.resolve(q["id"], "allow") is True
        return await t, reg, relay, q["id"]

    out, reg, relay, qid = asyncio.run(go())
    assert out == hook_response("allow")
    assert reg.get_question(qid)["status"] == "answered" and relay.closed == [qid]
    assert reg.get_session("s1")["state"] == "running"


def test_deny_response_has_a_message():
    r = hook_response("deny")
    assert r["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert r["hookSpecificOutput"]["decision"]["message"]


def test_timeout_returns_no_decision_and_expires_question():
    async def go():
        b, reg, relay = make(timeout=0.05)
        return await b.on_permission_request(PAYLOAD), reg, relay

    out, reg, relay = asyncio.run(go())
    assert out == {} and reg.get_question(1)["status"] == "expired" and relay.closed == [1]


def test_duplicate_delivery_shares_one_question_and_one_prompt():
    async def go():
        b, reg, relay = make()
        t1 = asyncio.create_task(b.on_permission_request(PAYLOAD))
        t2 = asyncio.create_task(b.on_permission_request(PAYLOAD))
        await asyncio.sleep(0)
        b.resolve(1, "deny")
        return await t1, await t2, relay

    r1, r2, relay = asyncio.run(go())
    assert r1 == r2 == hook_response("deny") and relay.raised == [1]


def test_resolve_unknown_or_finished_returns_false():
    b, _, _ = make()
    assert b.resolve(99, "allow") is False


def test_stop_and_session_end_update_registry():
    async def go():
        b, reg, _ = make()
        await b.on_event("SessionStart", {"session_id": "s2", "cwd": "/x"})
        await b.on_event("Stop", {"session_id": "s2", "last_assistant_message": "done"})
        assert reg.get_session("s2")["state"] == "idle"
        await b.on_event("SessionEnd", {"session_id": "s2", "reason": "other"})
        return reg

    reg = asyncio.run(go())
    s = reg.get_session("s2")
    assert (s["state"], s["end_reason"], s["origin"]) == ("ended", "other", "external")


def test_router_rejects_wrong_token_and_accepts_right_one():
    b, reg, _ = make()
    app = FastAPI()
    app.include_router(make_router(b, "sekret"))
    c = TestClient(app)
    assert c.post("/hook/wrong/Stop", json={"session_id": "s3"}).status_code == 404
    ok = c.post("/hook/sekret/Stop", json={"session_id": "s3"})
    assert ok.status_code == 200 and ok.json() == {}
    assert reg.get_session("s3")["state"] == "idle"


def test_router_ignores_payload_without_session_id():
    b, _, _ = make()
    app = FastAPI()
    app.include_router(make_router(b, "t"))
    assert TestClient(app).post("/hook/t/Stop", json={}).json() == {}


def test_token_is_created_once_with_private_mode(tmp_path):
    p = tmp_path / "hook-token"
    t1, t2 = load_token(p), load_token(p)
    assert t1 == t2 and len(t1) >= 32
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_run_settings_point_at_token_url(tmp_path):
    path = write_run_settings(tmp_path, "sess-1", "tok", 7860)
    hooks = json.loads(open(path).read())["hooks"]
    assert set(hooks) >= {"PermissionRequest", "Stop", "SessionEnd", "SessionStart"}
    h = hooks["PermissionRequest"][0]["hooks"][0]
    assert h["type"] == "http" and h["url"] == "http://127.0.0.1:7860/hook/tok/PermissionRequest"
    assert h["timeout"] == 100


def test_cancelled_handler_cleans_up_waiter_question_and_relay():
    async def go():
        b, reg, relay = make()
        t = asyncio.create_task(b.on_permission_request(PAYLOAD))
        await asyncio.sleep(0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return b, reg, relay

    b, reg, relay = asyncio.run(go())
    assert b._waiters == {} and reg.get_question(1)["status"] == "expired" and relay.closed == [1]
    assert reg.get_session("s1")["state"] == "running"


def test_relay_failure_does_not_leave_a_zombie_question():
    class BoomRelay(FakeRelay):
        async def raise_question(self, qid):
            raise RuntimeError("boom")

    async def go():
        reg, relay = Registry(), BoomRelay()
        b = HookBridge(reg, relay, permission_timeout=5)
        try:
            await b.on_permission_request(PAYLOAD)
        except RuntimeError:
            pass
        return b, reg, relay

    b, reg, relay = asyncio.run(go())
    assert b._waiters == {} and reg.get_question(1)["status"] == "expired" and relay.closed == [1]


def test_expire_session_releases_only_that_sessions_waiters():
    async def go():
        b, reg, relay = make()
        other = dict(PAYLOAD, session_id="s2")
        t1 = asyncio.create_task(b.on_permission_request(PAYLOAD))
        t2 = asyncio.create_task(b.on_permission_request(other))
        await asyncio.sleep(0)
        b.expire_session("s1")
        r1 = await asyncio.wait_for(t1, 2)
        assert not t2.done()
        b.resolve(2, "allow")
        return r1, await t2

    r1, r2 = asyncio.run(go())
    assert r1 == {} and r2 == hook_response("allow")


def test_settings_file_is_private_and_access_log_hides_tokens(tmp_path):
    import logging

    from hooks import hide_hook_tokens

    path = write_run_settings(tmp_path, "s", "tok", 7860)
    assert stat.S_IMODE(__import__("os").stat(path).st_mode) == 0o600
    fmt = '%s - "%s %s HTTP/%s" %d'  # uvicorn's access-log record
    hook_line = logging.LogRecord("uvicorn.access", 20, "", 0, fmt, ("127.0.0.1:1", "POST", "/hook/sekret/Stop", "1.1", 403), None)
    other = logging.LogRecord("uvicorn.access", 20, "", 0, fmt, ("127.0.0.1:1", "GET", "/", "1.1", 200), None)
    # Hook calls stay in the log (a refused hook must be visible) with the token blanked out.
    assert hide_hook_tokens(hook_line) is True and hide_hook_tokens(other) is True
    assert "sekret" not in hook_line.getMessage() and "/hook/…/Stop" in hook_line.getMessage() and "403" in hook_line.getMessage()


def test_a_timeout_tells_the_relay_the_question_expired_but_an_answer_does_not():
    async def go():
        b, reg, relay = make(timeout=0.05)
        await b.on_permission_request(PAYLOAD)
        t = asyncio.create_task(b.on_permission_request({**PAYLOAD, "tool_input": {"command": "ls"}}))
        await asyncio.sleep(0)
        b.resolve(reg.open_questions()[0]["id"], "deny")
        await t
        return relay

    relay = asyncio.run(go())
    assert relay.closed == [1, 2] and relay.expired == [1]


def test_a_non_ascii_token_in_the_url_is_rejected_not_a_server_error():
    b, reg, _ = make()
    app = FastAPI()
    app.include_router(make_router(b, "sekret"))
    assert TestClient(app).post("/hook/sékret/Stop", json={"session_id": "s3"}).status_code == 404


def test_an_existing_token_file_is_made_private_again(tmp_path):
    p = tmp_path / "hook-token"
    load_token(p)
    p.chmod(0o644)
    load_token(p)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_web_requests_are_described_in_words_not_json():
    from hooks import describe
    assert describe("WebFetch", {"url": "https://example.com/deals", "prompt": "find prices"}) == "WebFetch: https://example.com/deals"
    assert describe("WebSearch", {"query": "cheap flights to Goa"}) == "WebSearch: cheap flights to Goa"
    assert describe("Edit", "not a dict").startswith("Edit")  # never crashes into a silent denial
