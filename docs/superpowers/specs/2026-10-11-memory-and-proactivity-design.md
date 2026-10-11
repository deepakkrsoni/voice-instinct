# voice-instinct: Memory and Proactivity: Design

Builds on `2026-10-11-voice-instinct-poc-design.md`. Today the assistant only reacts: it acts when
spoken to and forgets everything between sessions. This spec adds the three things reviewers
credit Instinct, Grok Bot, and Hermes Agent with: memory, a scheduler that wakes the agent without
you, and a way to reach you, plus the consent rules that make proactive behavior safe.

## Goal

The assistant remembers what matters about you, acts on things you asked for at the time you
asked (reminders, recurring checks), occasionally raises something useful unprompted, and never
does anything with side effects without your say-so. Everything stays on your machine, in plain
files you can read, edit, and delete.

## Success criteria

1. "Remember that I'm vegetarian" in one session; in a new session "what do you remember about
   me?" lists it.
2. "Remind me at 3pm to call my mom" produces exactly one spoken or notified reminder at 3pm,
   with or without a browser page open.
3. "Forget that" removes the fact from disk; one command wipes all memory and jobs.
4. During quiet hours (default 22:00 to 08:00) nothing is spoken aloud; reminders become
   silent notifications.
5. A scheduled job that wants a side effect (write, shell) never runs it: it becomes a pending
   approval delivered to you, resolved by your next spoken yes/no (the existing confirmation rule).
6. The daily-review job is silent when it has nothing valuable to say.

## Non-goals

Calendar/email/payment/phone integrations, phone push, multi-user, cloud hosting, cron
expressions, parallel background tasks, learning new skills automatically (Hermes-style).

## Decisions (assumptions to overrule)

- **Storage:** plain files in `~/.voice-instinct/`, no database. Open-source friendly, user-editable.
- **Wake brain:** scheduled wakes use the existing Claude Code backend adapter, read-only, so
  there is no second LLM client. No CLI means proactive features are off, not broken.
- **Delivery:** local-first. Speak through the machine's speakers with the local Kokoro, or send a
  desktop notification (`notify-send`). Live session: inject into the open conversation instead.
- **Build order:** three independently shippable phases, one plan each (below).

## Architecture

```
user speech --remember/forget tools--> MemoryStore  (profile.md, log.md)
                                            |
Scheduler (asyncio tick, 30 s) --due--> Wake --prompt + memory--> backend (read-only)
   ^        jobs.json                         |
   |                                          v
 schedule/list/cancel tools            Policy (quiet hours, daily cap, SILENT)
                                              |
                              Notifier: live session | speak locally | notify-send
                                              |
                          side effect wanted? -> inbox.json pending approval
```

## Components

**MemoryStore (`memory.py`).** `profile.md`: short curated bullet facts, injected into the system
prompt at session start (capped at about 2,000 tokens, oldest dropped from the injection, never from
disk). `log.md`: append-only dated lines (events, session summaries); its last 20 lines are injected.
Tools for the front LLM: `remember(fact)`, `forget(text)` (removes lines containing the text,
returns how many), `what_do_you_know()`. Writing memory is autonomous (Tier 0); every write is
logged so the user can audit it. `wipe()` deletes the directory's contents.

**Jobs (`jobs.py`).** `jobs.json` is a list of `{id, prompt, once_at | daily_at, last_run}`. Pure
functions: `due(jobs, now)` and `mark_run(job, now)`. Times are local, 24h. No cron syntax.
Tools: `schedule(when, what)`, `list_jobs()`, `cancel_job(id)`. A built-in `daily_review` job
exists but is disabled by default and must be switched on by the user.

**Scheduler (`scheduler.py`).** One asyncio loop in the bot process, ticking every 30 s, calling a
`wake(job)` for each due job and recording `last_run` before running (a crash must not cause a repeat
firing). One wake at a time, using the same single-slot rule as `delegate`.

**Wake (`wake.py`).** Builds a prompt from the job text, the profile, and the log tail, with the
instruction: reply with one or two spoken sentences, or the single word `SILENT` if there is
nothing worth saying. Runs the backend read-only. Denials become inbox entries, never executions.

**Policy (`policy.py`).** Pure function `decide(now, history, quiet_hours, daily_cap) ->
speak | notify | drop`. Quiet hours turn speaking into a silent notification. A daily cap
(default 5 unprompted interruptions) turns further non-reminder wakes into drops. Explicit
reminders the user asked for are exempt from the cap but not from quiet hours' no-speech rule.

**Notifier (`notify.py`).** Interface `deliver(text, mode)`. Implementations: live session
(append a message to the active Pipecat context and run the LLM so it says it naturally);
local speech (Kokoro to speakers via `paplay`); desktop notification via `notify-send`.

**Inbox (`inbox.json`).** Pending approvals created by wakes: `{id, task, denials, created}`.
At session start, the assistant mentions unresolved items. Resolution reuses the existing
confirmation flow: only an explicit spoken yes after the question, deciding the exact command.
Entries expire after 24 h.

## Consent rules (the Instinct lessons)

- Tier 0, autonomous: reading, researching read-only, notifying, speaking outside quiet hours,
  writing and deleting its own memory.
- Tier 1, ask first: anything with side effects. Unchanged confirmation rules apply.
- Never proactive: sending messages or email, spending money. These have no tools yet; adding one
  needs its own spec.
- Memory only contains what the user said to remember or short session summaries, never raw
  access to other accounts. Deletion exists from day one (`forget`, `wipe`).

## Error handling

Corrupt or missing store files: start empty, keep a `.bak` of the bad file, log it. Backend
unavailable at wake time: skip the wake and record `last_run`, so it does not retry in a loop.
Missed jobs (machine asleep): a one-shot that is overdue by less than 1 h fires on next tick,
otherwise it is dropped and noted in the log. Two jobs due together: run sequentially.

## Testing

Pure unit tests with an injected clock for `due`, `mark_run`, `decide`, quiet hours, cap, and
overdue handling. `MemoryStore` against a temp directory (remember, forget, cap on injection,
corrupt-file recovery, wipe). Scheduler tick with a fake backend and fake notifier: fires once,
records `last_run` before running, SILENT produces no delivery, a denial lands in the inbox and
nothing executes. Manual: success criteria 1 to 6 by voice.

## Phases (one plan each)

1. **Memory:** MemoryStore, the three tools, prompt injection, wipe command. Criteria 1 and 3.
2. **Reminders:** Jobs, Scheduler, Wake, Policy, Notifier, the three schedule tools. Criteria 2 and 4.
3. **Proactive review and inbox:** `daily_review`, Inbox, session-start mention. Criteria 5 and 6.

## Open questions

- Is there a notification daemon on this Sway setup for `notify-send`? Check before phase 2.
- How the backend agent should read memory (inject text in the prompt, or grant it the directory).
  Default: inject text, keep the directory off its allowlist.
- Whether speaking from a headless process works with the current audio setup (the PipeWire
  and SoundWire notes in your memory suggest it needs a real test).
