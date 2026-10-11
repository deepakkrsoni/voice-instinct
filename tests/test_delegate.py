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


def make_params(*user_texts):
    props = []
    out = []

    async def cb(r, properties=None, **kw):
        out.append(r)
        props.append(properties)

    msgs = [{"role": "user", "content": t} for t in user_texts]
    ctx = SimpleNamespace(get_messages=lambda: msgs)
    return SimpleNamespace(result_callback=cb, context=ctx, props=props), out


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
    run(delegate(make_params("make a file")[0], "make a file"))
    p, out = make_params("make a file", "yeah go ahead")
    run(confirm(p))
    assert out[-1] == {"status": "done", "result": "created"}
    assert b.calls[1][2] == ("Bash(touch /tmp/x)",)
    assert "touch /tmp/x" in b.calls[1][0]  # retry names the exact approved command


def test_confirm_no_cancels_and_clears_pending():
    b = FakeBackend([BackendResult("blocked", "s1", [DENIAL])])
    delegate, confirm = make_tools(b)
    run(delegate(make_params("make a file")[0], "make a file"))
    p, out = make_params("make a file", "no don't")
    run(confirm(p))
    assert out[-1]["status"] == "cancelled"
    p2, out2 = make_params("make a file", "no don't", "yes")  # stale yes after a no must not resurrect the task
    run(confirm(p2))
    assert out2[-1]["status"] == "nothing_pending" and len(b.calls) == 1


def test_confirm_unclear_keeps_pending():
    b = FakeBackend([BackendResult("blocked", "s1", [DENIAL]), BackendResult("created", "s1")])
    delegate, confirm = make_tools(b)
    run(delegate(make_params("make a file")[0], "make a file"))
    p, out = make_params("make a file", "hmm what would it do")
    run(confirm(p))
    assert out[-1]["status"] == "unclear"
    p2, out2 = make_params("make a file", "hmm what would it do", "sure")
    run(confirm(p2))
    assert out2[-1]["status"] == "done"


def test_same_turn_confirm_cannot_approve():
    # The "yes" in the request that started the task is not an answer to the question.
    b = FakeBackend([BackendResult("blocked", "s1", [DENIAL]), BackendResult("created", "s1")])
    delegate, confirm = make_tools(b)
    run(delegate(make_params("okay make a file")[0], "make a file"))
    p, out = make_params("okay make a file")  # user has not spoken since the question
    run(confirm(p))
    assert out[-1]["status"] == "unclear" and len(b.calls) == 1
    p2, out2 = make_params("okay make a file", "yes please")  # a real answer still works
    run(confirm(p2))
    assert out2[-1]["status"] == "done"


def test_denial_without_tool_name_does_not_crash():
    b = FakeBackend([BackendResult("blocked", "s1", [{"tool_input": {}}]), BackendResult("ok", "s1")])
    delegate, confirm = make_tools(b)
    run(delegate(make_params("x")[0], "x"))
    p, out = make_params("x", "yes")
    run(confirm(p))
    assert out[-1]["status"] in ("done", "error")


def test_delegate_acknowledges_immediately_then_reports():
    b = FakeBackend([BackendResult("big.iso", "s1")])
    delegate, _ = make_tools(b)
    p, out = make_params("biggest file?")
    run(delegate(p, "biggest file?"))
    assert [o["status"] for o in out] == ["started", "done"]
    assert p.props[0].is_final is False and p.props[0].run_llm is False  # ack must not re-run the LLM
    assert p.props[1] is None  # final result uses the defaults


class GatedBackend:
    def __init__(self, gate):
        self.gate, self.calls = gate, []

    async def run(self, task, session_id=None, extra_allowed=()):
        self.calls.append(task)
        await self.gate.wait()
        return BackendResult("done it", "s1")


def test_second_task_while_running_is_busy_then_allowed_after():
    async def go():
        gate = asyncio.Event()
        b = GatedBackend(gate)
        delegate, _ = make_tools(b)
        p1, out1 = make_params("a")
        p2, out2 = make_params("a", "b")
        t = asyncio.create_task(delegate(p1, "first"))
        await asyncio.sleep(0)
        await delegate(p2, "second")
        gate.set()
        await t
        p3, out3 = make_params("a", "b", "c")
        await delegate(p3, "third")
        return b, out1, out2, out3

    b, out1, out2, out3 = run(asyncio.wait_for(go(), 3))  # RED must fail, not hang
    assert [o["status"] for o in out2] == ["busy"] and b.calls == ["first", "third"]
    assert out1[-1]["status"] == "done" and out3[-1]["status"] == "done"


class RaisingBackend:
    async def run(self, task, session_id=None, extra_allowed=()):
        raise OSError("spawn failed")


def test_backend_crash_still_reports_error_and_frees_the_slot():
    delegate, _ = make_tools(RaisingBackend())
    p, out = make_params("x")
    run(delegate(p, "x"))  # must not raise: a background call that never reports hangs forever
    assert out[-1]["status"] == "error"
    p2, out2 = make_params("x", "y")
    run(delegate(p2, "y"))
    assert out2[-1]["status"] == "error"  # not "busy": the slot was released
