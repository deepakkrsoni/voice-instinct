"""The errand time budget: the agent cannot see a clock, so the PostToolUse hook tells it, once, when an
errand is nearly out of time. Task 11 spent its whole 300 s opening product pages and answered nothing."""

from budget import TimeBudget


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make():
    clock = Clock()
    return TimeBudget(clock=clock), clock


def step(sid="s1"):
    return {"session_id": sid, "tool_name": "mcp__browser__browser_navigate", "tool_input": {}}


def test_no_warning_while_there_is_time():
    b, clock = make()
    b.start("s1", timeout=900)
    clock.now = 600  # two thirds in
    assert b.on_step(step()) == {}


def test_one_warning_once_three_quarters_of_the_time_is_gone():
    b, clock = make()
    b.start("s1", timeout=900)
    clock.now = 690
    out = b.on_step(step())
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "about 4 minutes" in hso["additionalContext"] and "answer now" in hso["additionalContext"]
    clock.now = 700
    assert b.on_step(step()) == {}  # once: the agent is not nagged at every step


def test_the_last_minute_is_said_as_one_minute():
    b, clock = make()
    b.start("s1", timeout=100)
    clock.now = 90
    assert "about 1 minute." in b.on_step(step())["hookSpecificOutput"]["additionalContext"]


def test_a_resumed_errand_gets_a_fresh_budget():
    b, clock = make()
    b.start("s1", timeout=900)
    clock.now = 800
    assert b.on_step(step())  # warned in the first run
    b.start("s1", timeout=900)  # the user's follow-up resumes the same session
    clock.now = 900
    assert b.on_step(step()) == {}


def test_the_hook_route_returns_the_warning_and_still_records_the_step():
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from hooks import HookBridge, make_router
    from registry import Registry

    recorded = []

    async def on_step(p):
        recorded.append(p["tool_name"])
        return {}

    b, clock = make()
    b.start("s1", timeout=100)
    clock.now = 80
    app = FastAPI()
    app.include_router(make_router(HookBridge(Registry(), None), "tok", steps=SimpleNamespace(on_step=on_step),
                                   budget=b))
    out = TestClient(app).post("/hook/tok/PostToolUse", json=step()).json()
    assert out["hookSpecificOutput"]["additionalContext"].startswith("Time check")
    assert recorded == ["mcp__browser__browser_navigate"]  # debug mode still saw the step
    plain = FastAPI()  # no debug, budget only
    plain.include_router(make_router(HookBridge(Registry(), None), "tok", budget=TimeBudget()))
    assert TestClient(plain).post("/hook/tok/PostToolUse", json=step("other")).json() == {}


def test_steps_from_unknown_or_finished_errands_are_ignored():
    b, clock = make()
    assert b.on_step(step("nobody")) == {}
    b.start("s1", timeout=10)
    b.finish("s1")
    clock.now = 9
    assert b.on_step(step()) == {}
