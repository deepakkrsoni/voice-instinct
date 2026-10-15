import asyncio
import os
import subprocess
from datetime import date, datetime

from claude_cli import ClaudeCodeBackend
from consolidate import consolidate, pending
from memory import Memory

TODAY = date(2026, 10, 16)


def mem(tmp_path):
    m = Memory(tmp_path / "memory", zone="Asia/Kolkata", now=lambda: datetime(2026, 10, 16, 3, 0))
    m.ensure()
    (m.root / "journal").mkdir()
    for d in ("2026-10-01", "2026-10-14", "2026-10-15", "2026-10-16"):
        (m.root / "journal" / f"{d}.md").write_text(f"10:00 user: my sister Priya lives in Pune ({d})\n")
    (m.root / ".consolidated").write_text("2026-10-01\n2026-10-14\n")
    m.commit("setup")
    return m


def fake_claude(tmp_path, body):
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\n" + body)
    cli.chmod(0o755)
    return ClaudeCodeBackend(cmd=str(cli), permission_mode="acceptEdits", system_prompt="x")


OK = ('mkdir -p people && printf "# Priya\\naliases: sister\\n- 2026-10-15: lives in Pune\\n" > people/priya.md\n'
      'echo "$*" > ../args\n'
      'echo \'{"type":"result","is_error":false,"result":"merged"}\'\n')
FAIL = ('echo broken > profile.md; mkdir -p people && echo half > people/half.md\n'
        'echo \'{"type":"result","is_error":true,"result":"Prompt is too long"}\'\n')


def log(m):
    return subprocess.run(["git", "-C", str(m.root), "log", "--format=%s"], capture_output=True, text=True).stdout


def test_only_finished_days_not_yet_consolidated_are_read(tmp_path):
    assert [p.stem for p in pending(mem(tmp_path), TODAY)] == ["2026-10-15"]


def test_a_run_files_the_facts_clears_the_consumed_notes_records_the_day_and_prunes_old_journals(tmp_path):
    m = mem(tmp_path)
    m.remember("Is vegetarian")
    assert asyncio.run(consolidate(m, fake_claude(tmp_path, OK), TODAY)) is True
    assert "lives in Pune" in (m.root / "people/priya.md").read_text()
    assert (m.root / "notes.md").read_text() == ""
    assert (m.root / ".consolidated").read_text().splitlines()[-1] == "2026-10-15"
    assert "consolidate 2026-10-15" in log(m)
    assert not (m.root / "journal/2026-10-01.md").exists()  # consolidated and over 7 days old
    assert (m.root / "journal/2026-10-14.md").exists() and (m.root / "journal/2026-10-16.md").exists()
    args = (tmp_path / "memory/../args").read_text()
    assert "journal/2026-10-15.md" in args and "notes.md" in args and "2026-10-14" not in args


def test_a_note_added_while_the_run_was_going_is_kept(tmp_path):
    m = mem(tmp_path)
    m.remember("Is vegetarian")
    late = OK.replace("echo \"$*\"", "echo '- 2026-10-16: Likes chai' >> notes.md; echo \"$*\"")
    assert asyncio.run(consolidate(m, fake_claude(tmp_path, late), TODAY)) is True
    assert (m.root / "notes.md").read_text() == "- 2026-10-16: Likes chai\n"


def test_a_failed_run_changes_nothing_and_is_retried_next_time(tmp_path):
    m = mem(tmp_path)
    m.remember("Is vegetarian")
    profile = (m.root / "profile.md").read_text()
    assert asyncio.run(consolidate(m, fake_claude(tmp_path, FAIL), TODAY)) is False
    assert (m.root / "profile.md").read_text() == profile and not (m.root / "people/half.md").exists()
    assert "Is vegetarian" in (m.root / "notes.md").read_text()
    assert [p.stem for p in pending(m, TODAY)] == ["2026-10-15"]
    assert (m.root / "journal/2026-10-16.md").exists()  # the journal is never cleaned away


def test_nothing_new_means_no_run(tmp_path):
    m = mem(tmp_path)
    (m.root / ".consolidated").write_text("2026-10-01\n2026-10-14\n2026-10-15\n")
    assert asyncio.run(consolidate(m, fake_claude(tmp_path, "exit 1\n"), TODAY)) is True
    assert not os.path.exists(tmp_path / "args")


def test_hand_edits_survive_a_failed_run(tmp_path):
    m = mem(tmp_path)
    m.remember("Is vegetarian")
    (m.root / "profile.md").write_text("# Profile\n- edited by hand\n")
    (m.root / "people").mkdir()
    (m.root / "people/mum.md").write_text("# Mum\n- added by hand\n")
    assert asyncio.run(consolidate(m, fake_claude(tmp_path, FAIL), TODAY)) is False
    assert (m.root / "profile.md").read_text() == "# Profile\n- edited by hand\n"
    assert (m.root / "people/mum.md").exists() and not (m.root / "people/half.md").exists()


def test_a_note_remembered_during_a_failed_run_survives_the_rollback(tmp_path):
    m = mem(tmp_path)
    m.remember("Is vegetarian")
    late_fail = "echo '- 2026-10-16: Likes chai' >> notes.md\n" + FAIL
    assert asyncio.run(consolidate(m, fake_claude(tmp_path, late_fail), TODAY)) is False
    assert (m.root / "notes.md").read_text() == "- 2026-10-16: Is vegetarian\n- 2026-10-16: Likes chai\n"
    assert (m.root / "profile.md").read_text() != "broken\n"


def test_only_one_run_at_a_time(tmp_path):
    m = mem(tmp_path)
    with m.tidying():
        assert asyncio.run(consolidate(m, fake_claude(tmp_path, OK), TODAY)) is False
    assert not (tmp_path / "args").exists()


def test_notes_are_kept_when_the_run_filed_nothing(tmp_path):
    m = mem(tmp_path)
    m.remember("Is vegetarian")
    idle = 'echo \'{"type":"result","is_error":false,"result":"nothing to do"}\'\n'
    assert asyncio.run(consolidate(m, fake_claude(tmp_path, idle), TODAY)) is True
    assert "Is vegetarian" in (m.root / "notes.md").read_text()
    assert pending(m, TODAY) == []  # the journal day itself is done
