# Contributing to voice-instinct

Thanks for helping. This is a voice-first personal agent that does Instinct-style errands on your own
machine. The project is early, so a small, well-tested change is worth more than a big one.

## What to work on

- Pick an issue from the [roadmap](README.md#roadmap). Issues labelled `needs-design` have open
  questions: comment with your approach before building, so we agree on the design first.
- Scope is **errands only** (research, bookings, shopping, bills, subscriptions, reminders, calling
  businesses). Coding assistants and general computer tasks are out of scope; see
  [the scope spec](docs/superpowers/specs/2026-10-13-instinct-scope.md).
- Smaller things welcome without an issue: docs, tests, making hardcoded model paths configurable,
  error messages, bug reports with steps to reproduce.

## Set up

You need Linux, [uv](https://docs.astral.sh/uv/), the `claude` CLI logged in, `bubblewrap` and
`socat` (the sandbox refuses to start without them), plus the local models described in the README.

```
git clone https://github.com/deepakkrsoni/voice-instinct && cd voice-instinct
uv sync
echo 'GEMINI_API_KEY=...' > .env     # never commit this
uv run bot.py                        # http://localhost:7860/client
```

## Tests

```
uv run pytest            # unit tests, no network, no models needed
uv run pytest -m claude  # real-CLI tests; they use your Claude subscription
```

Write the failing test first, watch it fail for the right reason, then make it pass. New behavior
needs a test; a bug fix needs a test that failed before the fix. Do not mock what you can run: the
fakes in `tests/` stand in for Pipecat and the CLI only at the edges.

## Rules that protect the user (please keep them true)

- **Approvals are decided by code, never by the LLM.** A yes or no counts only if the assistant asked
  and the user replied afterwards (`relay.py`, `confirm.py`).
- **Anything coming from the agent, a web page or a tool is untrusted data.** It reaches the LLM only
  inside the quoted block in `relay.py`, never as instructions.
- **Hooks fail open.** If this app is down, the user's Claude sessions must keep working.
- **No money, messages or account changes without a code-enforced limit and an action log.** Do not
  add a payment or send tool before the safeguards in the roadmap exist.
- **No secrets in the repo, logs or settings files.** Tokens and databases are mode 600/700; check
  `git diff` before you commit.

## Using Pipecat

Use Pipecat's documented ways before inventing your own. The full docs are one greppable file:
`curl -sL https://docs.pipecat.ai/llms-full.txt -o llms-full.txt`. If you must build something the docs
do not cover, say so in your PR and explain why.

## Pull requests

1. Branch from `main` and keep the PR focused on one issue; reference it (`Closes #N`).
2. Run `uv run pytest` and, if you touched the backend or hooks, `uv run pytest -m claude`.
3. Describe what you verified and what you did not (for example, not tried by voice).
4. Be kind in review; we iterate in the open.

## Security

Do not open a public issue for a vulnerability. Contact the maintainer through their GitHub profile instead.
(GitHub's private vulnerability reporting is not enabled on this repository yet; once it is, use the
Security tab.)

## License

A license has not been chosen yet ([#11](https://github.com/deepakkrsoni/voice-instinct/issues/11)).
Until then, please hold off on large contributions; small fixes are fine, and the license decision
will be recorded here.
