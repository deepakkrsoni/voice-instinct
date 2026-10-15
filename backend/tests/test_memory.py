import asyncio
import subprocess
from datetime import datetime
from types import SimpleNamespace

from memory import CONTEXT_CAP, Memory, country_of, make_memory_tools

DAY = datetime(2026, 10, 16, 9, 5)


def mem(tmp_path, zone="Asia/Kolkata"):
    m = Memory(tmp_path / "memory", zone=zone, now=lambda: DAY)
    m.ensure()
    return m


def log(m):
    return subprocess.run(["git", "-C", str(m.root), "log", "--format=%s"], capture_output=True, text=True).stdout


def test_country_comes_from_the_timezone_through_the_system_tables():
    assert country_of("Asia/Kolkata") == "India"
    assert country_of("Europe/Berlin") == "Germany"
    assert country_of("") is None and country_of("Nowhere/Land") is None


def test_first_start_seeds_a_profile_with_location_and_commits_it(tmp_path):
    m = mem(tmp_path)
    profile = (m.root / "profile.md").read_text()
    assert "Lives in India (timezone Asia/Kolkata)" in profile and "currency" in profile
    assert "When to act and when to ask" in profile and "How to talk" in profile
    assert "memory: set up" in log(m)
    m.ensure()  # a second start keeps the user's own edits
    (m.root / "profile.md").write_text("# Profile\n- edited by hand\n")
    m.ensure()
    assert (m.root / "profile.md").read_text() == "# Profile\n- edited by hand\n"


def test_no_timezone_means_no_location_line(tmp_path):
    assert "Lives in" not in (mem(tmp_path, zone="").root / "profile.md").read_text()


def test_remember_is_in_the_context_at_once_and_committed(tmp_path):
    m = mem(tmp_path)
    m.remember("I'm vegetarian")
    assert "- 2026-10-16: I'm vegetarian" in m.context()
    assert "Lives in India" in m.context()
    assert "remember" in log(m)


def test_the_context_is_capped_but_the_files_are_not(tmp_path):
    m = mem(tmp_path)
    (m.root / "profile.md").write_text("# Profile\n" + "x" * (CONTEXT_CAP * 2))
    assert len(m.context()) <= CONTEXT_CAP
    assert len((m.root / "profile.md").read_text()) > CONTEXT_CAP


def test_forget_removes_the_fact_everywhere_but_keeps_structure(tmp_path):
    m = mem(tmp_path)
    m.remember("I'm vegetarian")
    (m.root / "people").mkdir()
    (m.root / "people/priya.md").write_text("# Priya\naliases: sister, vegetarian cook\n- 2026-10-15: vegetarian too\n"
                                            "- 2026-10-15: lives in Pune\n")
    m.journal("user", "remember that I'm vegetarian")
    assert len(m.forget("Vegetarian")) == 3  # notes, Priya's fact, the journal line; never the alias line
    assert "vegetarian" not in m.context().lower()
    priya = (m.root / "people/priya.md").read_text()
    assert priya.startswith("# Priya\naliases: sister, vegetarian cook\n") and "Pune" in priya
    assert "vegetarian" not in m.journal_path().read_text().lower()
    assert "forget" in log(m)


def test_forget_refuses_text_too_short_to_be_safe(tmp_path):
    m = mem(tmp_path)
    m.remember("I'm vegetarian")
    assert m.forget(" a ") == []
    assert "vegetarian" in m.context()


def test_recall_by_alias_returns_the_whole_file(tmp_path):
    m = mem(tmp_path)
    (m.root / "people").mkdir()
    (m.root / "people/priya.md").write_text("# Priya\naliases: sister\n- 2026-10-15: lives in Pune\n")
    (m.root / "places").mkdir()
    (m.root / "places/pune.md").write_text("# Pune\n- 2026-10-15: user's sister lives here\n")
    out = m.recall("what's my sister's name?")
    assert "people/priya.md: - 2026-10-15: lives in Pune" in out
    assert out.index("people/priya.md") < out.index("places/pune.md")  # the alias match ranks first
    assert "aliases:" not in out


def test_recall_finds_lines_by_word_and_skips_the_journal(tmp_path):
    m = mem(tmp_path)
    m.remember("my passport expires in March 2027")
    m.journal("user", "my passport is blue")
    out = m.recall("passport")
    assert "notes.md: - 2026-10-16: my passport expires in March 2027" in out
    assert "blue" not in out
    assert m.recall("zebra") == ""


def test_the_journal_is_dated_and_never_committed(tmp_path):
    m = mem(tmp_path)
    m.journal("user", "find me a T-shirt")
    m.journal("assistant", "On it.")
    assert m.journal_path().name == "2026-10-16.md"
    assert m.journal_path().read_text() == "09:05 user: find me a T-shirt\n09:05 assistant: On it.\n"
    m.remember("x fact")
    tracked = subprocess.run(["git", "-C", str(m.root), "ls-files"], capture_output=True, text=True).stdout
    assert "journal" not in tracked


def test_an_unusable_folder_turns_memory_off_without_raising(tmp_path):
    (tmp_path / "file").write_text("")
    m = Memory(tmp_path / "file" / "memory", zone="Asia/Kolkata")
    assert m.ensure() is False
    assert m.context() == "" and m.recall("x") == "" and m.forget("something") == []
    m.remember("x")  # no exception
    m.journal("user", "x")


def test_the_tools_answer_nova(tmp_path):
    m = mem(tmp_path)
    scrubbed = []
    remember, forget, recall = make_memory_tools(m, on_forget=scrubbed.append)
    got = []

    async def cb(result, **kw):
        got.append(result)

    p = SimpleNamespace(result_callback=cb)
    asyncio.run(remember(p, fact="my sister is Priya"))
    asyncio.run(recall(p, query="sister"))
    asyncio.run(forget(p, text="Priya"))
    assert got[0]["status"] == "remembered"
    assert "my sister is Priya" in got[1]["found"]
    assert got[2]["status"] == "forgotten" and got[2]["facts_removed"] == 1
    assert got[2]["removed"] == ["notes.md: - 2026-10-16: my sister is Priya"] and "never use" in got[2]["instruction"]
    assert scrubbed[0]("Priya called") and not scrubbed[0]("Asha called")  # the live conversation is scrubbed too
    asyncio.run(forget(p, text="Priya"))
    assert got[3]["status"] == "nothing_found"


def test_forget_in_the_users_own_words_finds_the_third_person_fact(tmp_path):
    # Review: "forget that I'm vegetarian" removed only the journal line and said "forgotten".
    m = mem(tmp_path)
    m.journal("user", "Remember that I'm vegetarian")
    m.remember("Is vegetarian")
    removed = m.forget("that I'm vegetarian")
    assert "notes.md: - 2026-10-16: Is vegetarian" in removed and "vegetarian" not in m.context()
    m.remember("José is my cousin")
    assert m.forget("José") == ["notes.md: - 2026-10-16: José is my cousin"]  # not only ASCII words


def test_while_the_nightly_run_tidies_the_tools_never_commit_and_forget_waits(tmp_path):
    m = mem(tmp_path)
    m.remember("Is vegetarian")
    before = log(m)
    with m.tidying():
        m.remember("Likes chai")
        assert log(m) == before  # its note is written, and the run commits it with its own work
        assert "Likes chai" in m.context()
        assert m.forget("vegetarian") is None  # the run could write it back: ask again in a few minutes
    assert len(m.forget("vegetarian")) == 1
    assert ".lock" in (m.root / ".gitignore").read_text()


def test_a_users_commit_signing_setting_does_not_stop_commits(tmp_path):
    m = mem(tmp_path)
    subprocess.run(["git", "-C", str(m.root), "config", "commit.gpgsign", "true"], check=True)
    subprocess.run(["git", "-C", str(m.root), "config", "gpg.program", "false"], check=True)
    m.remember("Is vegetarian")
    assert "remember" in log(m)


def test_an_unreadable_memory_file_never_breaks_the_prompt_or_recall(tmp_path):
    m = mem(tmp_path)
    (m.root / "people").mkdir()
    (m.root / "people/bad.md").write_bytes(b"# Bad\n\xff\xfe broken\n")
    (m.root / "notes.md").write_bytes(b"\xff\xfe")
    assert "Lives in India" in m.context()
    assert m.recall("broken") == "" and m.forget("broken") == []


def test_forget_matches_whole_words_and_refuses_common_words(tmp_path):
    # Review: "forget that" passed through as "that" wiped unrelated facts, "India" the location line.
    m = mem(tmp_path)
    m.remember("likes that cafe near Indiranagar")
    m.remember("Is vegetarian")
    assert m.forget("that") == [] and m.forget("the") == [] and m.forget("my sister") == []
    assert m.forget("Indira") == []  # inside a word: not a match
    assert m.forget("vegetarian") == ["notes.md: - 2026-10-16: Is vegetarian"]
    assert "Indiranagar" in m.context() and "Lives in India" in m.context()


def test_a_git_failure_never_reaches_the_tool_call(tmp_path):
    # Review: a stale index.lock (or the nightly commit) made remember raise, and Nova said the model was down.
    m = mem(tmp_path)
    (m.root / ".git/index.lock").write_text("")
    m.remember("Is vegetarian")
    assert "Is vegetarian" in m.context()  # saved; the next commit picks it up
    assert len(m.forget("vegetarian")) == 1


def test_git_hooks_in_the_memory_repo_never_run(tmp_path):
    # The nightly run writes this folder from untrusted text: a planted hook must not run on our commits.
    m = mem(tmp_path)
    hook = m.root / ".git/hooks/pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {tmp_path}/pwned\n")
    hook.chmod(0o755)
    m.remember("Is vegetarian")
    assert not (tmp_path / "pwned").exists() and "remember" in log(m)


def test_recall_matches_whole_words_only(tmp_path):
    m = mem(tmp_path)
    (m.root / "people").mkdir()
    (m.root / "people/sandra.md").write_text("# Sandra\naliases: colleague\n- 2026-10-15: works at Infer\n")
    assert m.recall("my sister and her plans") == ""


def test_new_notes_survive_a_full_profile(tmp_path):
    m = mem(tmp_path)
    (m.root / "profile.md").write_text("# Profile\n" + "x" * CONTEXT_CAP)
    m.remember("Is vegetarian")
    assert "Is vegetarian" in m.context() and len(m.context()) <= CONTEXT_CAP
