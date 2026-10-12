import sqlite3

import pytest

from registry import Registry


def make(clock=None):
    t = [1000.0]
    return Registry(clock=clock or (lambda: t[0])), t


def test_event_for_unknown_session_is_rejected():
    r, _ = make()
    with pytest.raises(sqlite3.IntegrityError):
        r.add_event("nope", "Stop", {})


def test_upsert_creates_then_updates_without_clobbering():
    r, _ = make()
    r.upsert_session("s1", origin="delegated", cwd="/a", mode="headless")
    r.upsert_session("s1", state="idle")  # no cwd/origin given: they must survive
    s = r.get_session("s1")
    assert (s["origin"], s["cwd"], s["mode"], s["state"]) == ("delegated", "/a", "headless", "idle")


def test_unknown_session_defaults_to_external():
    r, _ = make()
    r.upsert_session("s9")
    assert r.get_session("s9")["origin"] == "external"


def test_end_sets_ended_at_and_reason():
    r, t = make()
    r.upsert_session("s1")
    t[0] = 1500.0
    r.set_state("s1", "ended", end_reason="other")
    s = r.get_session("s1")
    assert (s["state"], s["ended_at"], s["end_reason"]) == ("ended", 1500.0, "other")


def test_duplicate_open_question_is_idempotent_but_new_after_answer():
    r, _ = make()
    r.upsert_session("s1")
    a = r.add_question("s1", "permission", "run ls", "Bash", {"command": "ls"})
    b = r.add_question("s1", "permission", "run ls", "Bash", {"command": "ls"})
    assert a == b
    r.resolve_question(a, "answered", "allow")
    c = r.add_question("s1", "permission", "run ls", "Bash", {"command": "ls"})
    assert c != a
    assert r.add_question("s1", "permission", "run pwd", "Bash", {"command": "pwd"}) != c


def test_expire_open_only_touches_open():
    r, _ = make()
    r.upsert_session("s1")
    q1 = r.add_question("s1", "permission", "a", "Bash", {"command": "a"})
    q2 = r.add_question("s1", "permission", "b", "Bash", {"command": "b"})
    r.resolve_question(q1, "answered", "allow")
    assert r.expire_open() == 1
    assert r.get_question(q1)["status"] == "answered"
    assert r.get_question(q2)["status"] == "expired"
    assert r.open_questions() == []


def test_tasks_link_to_sessions_and_list_shows_counts():
    r, _ = make()
    r.upsert_session("s1", origin="delegated")
    tid = r.create_task("s1", "find the biggest file")
    r.add_question("s1", "permission", "x", "Bash", {"command": "x"})
    row = r.list_sessions()[0]
    assert row["open_questions"] == 1 and row["last_task"] == "find the biggest file"
    r.finish_task(tid, "done", "it was big.iso")
    r.set_state("s1", "ended")
    assert r.list_sessions(active_only=True) == []
    assert len(r.list_sessions()) == 1


def test_database_and_its_directory_are_private(tmp_path):
    import os
    import stat

    d = tmp_path / "data"
    Registry(d / "harness.db")
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(d / "harness.db").st_mode) == 0o600
