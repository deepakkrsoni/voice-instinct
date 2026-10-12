import asyncio
from types import SimpleNamespace

from backend import BackendResult
from delegate import make_tools
from registry import Registry


class FakeBackend:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def run(self, task, session_id=None, resume=False, settings=None, extra_allowed=()):
        self.calls.append({"task": task, "sid": session_id, "resume": resume, "settings": settings})
        return self.results.pop(0)


def make_params():
    out, props = [], []

    async def cb(r, properties=None, **kw):
        out.append(r)
        props.append(properties)

    return SimpleNamespace(result_callback=cb, props=props), out


def setup(results):
    reg = Registry()
    b = FakeBackend(results)
    (delegate,) = make_tools(b, reg, lambda sid: f"/settings/{sid}.json")
    return reg, b, delegate


def run(c):
    return asyncio.run(c)


def test_delegate_acknowledges_then_reports_and_records_everything():
    reg, b, delegate = setup([BackendResult("big.iso", "ignored")])
    p, out = make_params()
    run(delegate(p, "biggest file?"))
    assert [o["status"] for o in out] == ["started", "done"] and out[-1]["result"] == "big.iso"
    assert p.props[0].is_final is False and p.props[0].run_llm is False and p.props[1] is None
    call = b.calls[0]
    assert call["resume"] is False and call["settings"] == f"/settings/{call['sid']}.json"
    s = reg.get_session(call["sid"])
    assert (s["origin"], s["mode"], s["state"]) == ("delegated", "headless", "idle")
    assert reg.list_sessions()[0]["last_task"] == "biggest file?"


def test_follow_up_resumes_the_same_session():
    reg, b, delegate = setup([BackendResult("Which folder?"), BackendResult("done")])
    run(delegate(make_params()[0], "tidy up"))
    run(delegate(make_params()[0], "the downloads one"))
    assert b.calls[1]["sid"] == b.calls[0]["sid"] and b.calls[1]["resume"] is True
    assert len(reg.list_sessions()) == 1


class GatedBackend:
    def __init__(self, gate):
        self.gate, self.calls = gate, []

    async def run(self, task, session_id=None, resume=False, settings=None, extra_allowed=()):
        self.calls.append(task)
        await self.gate.wait()
        return BackendResult("done it")


def test_second_task_while_running_is_busy_then_allowed_after():
    async def go():
        gate = asyncio.Event()
        b = GatedBackend(gate)
        (delegate,) = make_tools(b, Registry(), lambda sid: None)
        p1, out1 = make_params()
        p2, out2 = make_params()
        t = asyncio.create_task(delegate(p1, "first"))
        await asyncio.sleep(0)
        await delegate(p2, "second")
        gate.set()
        await t
        p3, out3 = make_params()
        await delegate(p3, "third")
        return b, out1, out2, out3

    b, out1, out2, out3 = run(asyncio.wait_for(go(), 3))
    assert [o["status"] for o in out2] == ["busy"] and b.calls == ["first", "third"]
    assert out1[-1]["status"] == "done" and out3[-1]["status"] == "done"


class RaisingBackend:
    async def run(self, *a, **k):
        raise OSError("spawn failed")


def test_backend_crash_reports_error_frees_the_slot_and_marks_task_failed():
    reg = Registry()
    (delegate,) = make_tools(RaisingBackend(), reg, lambda sid: None)
    p, out = make_params()
    run(delegate(p, "x"))
    assert out[-1]["status"] == "error"
    p2, out2 = make_params()
    run(delegate(p2, "y"))
    assert out2[-1]["status"] == "error"  # not "busy"
    statuses = [r[0] for r in reg.db.execute("SELECT status FROM tasks ORDER BY id")]
    assert statuses == ["error", "error"]
