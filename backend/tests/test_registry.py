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


def test_the_conversation_round_trips_and_survives_a_restart(tmp_path):
    db = tmp_path / "harness.db"
    msgs = [{"role": "user", "content": "find t-shirts"}, {"role": "assistant", "content": "On it."}]
    Registry(db).save_conversation(msgs)
    assert Registry(db).load_conversation() == msgs  # a new Registry: as after a backend restart


def test_no_conversation_yet_is_empty():
    reg, _ = make()
    assert reg.load_conversation() == []


def test_a_corrupt_conversation_starts_empty_and_is_kept_aside(tmp_path):
    db = tmp_path / "harness.db"
    reg = Registry(db)
    reg.save_conversation([{"role": "user", "content": "hi"}])
    reg.db.execute("UPDATE conversation SET messages_json = '{not json' WHERE id = 1")
    assert Registry(db).load_conversation() == []
    bad = reg.db.execute("SELECT messages_json FROM conversation_bad").fetchall()
    assert [r[0] for r in bad] == ["{not json"]


def test_a_task_is_found_by_its_id():
    reg, _ = make()
    reg.upsert_session("s1", cwd="/w")
    tid = reg.create_task("s1", "find t-shirts")
    assert reg.get_task(tid)["session_id"] == "s1" and reg.get_task(tid)["request"] == "find t-shirts"
    assert reg.get_task(tid + 99) is None


def test_reminders_add_list_due_cancel():
    reg = Registry(clock=lambda: 100.0)
    a = reg.add_reminder("call mom", 500.0, "none")
    b = reg.add_reminder("water", 200.0, "daily")
    assert [r["id"] for r in reg.pending_reminders()] == [b, a]  # soonest first
    assert [r["id"] for r in reg.due_reminders(300.0)] == [b]
    assert reg.cancel_reminder(a) and not reg.cancel_reminder(a)
    assert [r["id"] for r in reg.pending_reminders()] == [b]


def test_a_reminder_is_claimed_once_and_can_be_put_back():
    reg = Registry()
    r = reg.add_reminder("x", 1.0, "none")
    assert reg.mark_fired(r, 2.0) and not reg.mark_fired(r, 2.0)
    assert reg.due_reminders(5.0) == []
    reg.unfire_reminder(r)
    assert [x["id"] for x in reg.due_reminders(5.0)] == [r]


def test_a_reminder_in_flight_is_done_once_delivered_or_put_back_on_restart():
    reg = Registry()
    a, b = reg.add_reminder("a", 1.0, "none"), reg.add_reminder("b", 1.0, "none")
    reg.mark_fired(a, 2.0)
    reg.mark_fired(b, 2.0)
    reg.mark_delivered(a)
    assert reg.requeue_in_flight() == 1  # b was never delivered: a restart puts it back
    assert [r["id"] for r in reg.due_reminders(5.0)] == [b]
