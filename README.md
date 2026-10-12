# voice-instinct

A voice-first personal agent that runs on your own machine and handles everyday errands, in the
spirit of [Instinct](https://techcrunch.com/2026/09/28/viral-ai-agent-instinct-raises-1b-series-c-at-a-10b-valuation/)
(the texting agent): you talk to it, it does the legwork, and it asks you, by voice, only when it
needs a decision, a choice or a login code. Local and private, open source, bring your own model.
Not affiliated with Instinct.

> **Status: early prototype.** The voice loop, the delegated-task bridge, the sandboxed autonomy and
> the approval-by-voice flow exist. The errand capabilities below are mostly not built yet; the
> roadmap shows what is next and where help is welcome.

## What it does today

- Talk to it in the browser; fast local speech-to-text (Parakeet) and text-to-speech (Kokoro) around
  a model of your choice (Gemini by default; any OpenAI-compatible endpoint or Ollama).
- Hands real work to your own logged-in `claude` CLI as a tracked background task, runs it
  autonomously in a sandbox, and speaks the result.
- When the agent needs a go-ahead or an answer, it asks you aloud and continues in the same run.
- Scope is **errands only** (research, bookings, shopping, bills, subscriptions, reminders, calling
  businesses). It declines coding and general computer tasks. See
  [docs/superpowers/specs/2026-10-13-instinct-scope.md](docs/superpowers/specs/2026-10-13-instinct-scope.md).

## Roadmap

Each item is a GitHub issue. Order follows the scope spec.

| Step | Capability | Issue |
|---|---|---|
| 1 | Research and comparison shopping, spoken results | [#2](https://github.com/deepakkrsoni/voice-instinct/issues/2) |
| 1 | Memory: remember, forget, recall | [#3](https://github.com/deepakkrsoni/voice-instinct/issues/3) |
| 1 | Reminders and proactive follow-ups | [#4](https://github.com/deepakkrsoni/voice-instinct/issues/4) |
| 1 | Text channel: Telegram first, WhatsApp later | [#5](https://github.com/deepakkrsoni/voice-instinct/issues/5) |
| 2 | Browser bookings: propose, then book on your go-ahead | [#6](https://github.com/deepakkrsoni/voice-instinct/issues/6) |
| 3 | Payments with code-enforced spend limits and an action log | [#7](https://github.com/deepakkrsoni/voice-instinct/issues/7) |
| 4 | Concierge phone calls to businesses | [#8](https://github.com/deepakkrsoni/voice-instinct/issues/8) |
| 5 | Cancel subscriptions by voice | [#9](https://github.com/deepakkrsoni/voice-instinct/issues/9) |
| later | Trusted-person network (agent-to-agent) | [#10](https://github.com/deepakkrsoni/voice-instinct/issues/10) |
| meta | Choose a license | [#11](https://github.com/deepakkrsoni/voice-instinct/issues/11) |

Want to help? Read [CONTRIBUTING.md](CONTRIBUTING.md).

## How it works

```
mic -> Parakeet STT -> Smart Turn v3 -> front LLM -> Kokoro TTS -> speaker
                                          |  ^
                                 delegate(task)  result / questions
                                          v  |
                  Claude Code CLI (your login) in a sandboxed task folder
                          hooks -> local relay -> asks you by voice
```

Built on [Pipecat](https://docs.pipecat.ai) 1.12. The design documents are in `docs/superpowers/`.

## Run

```
uv sync
echo 'GEMINI_API_KEY=...' > .env      # or whatever api_key_env names in config.toml
uv run bot.py                         # then open http://localhost:7860/client
```

Needs: Parakeet TDT 0.6B v3 (sherpa-onnx int8) in `~/.local/share/parakeet-dictation/models/desktop/`,
Kokoro (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) in `~/kokoro/`, and the `claude` CLI logged in.
Model paths are hardcoded for now (`services.py` and `bot.py`); making them configurable is a good first contribution.

## Config (`config.toml`)

| key | meaning |
|---|---|
| `provider` | `gemini`, `openai`, or `ollama` (front model) |
| `model` | model id, default `gemini-3.8-flash` |
| `base_url` | OpenAI-compatible endpoint (Ollama defaults to `localhost:11434`) |
| `api_key_env` | env var holding the key |
| `permission_mode` | Claude Code mode for delegated runs (default `auto`) |
| `workspace_dir` | default task folder (default `~/.voice-instinct/workspace`) |
| `backend_cwd`, `backend_timeout` | where the backend agent works (default home) and its timeout (300 s; keep it well above `permission_timeout`, 90 s) |

## Safety and autonomy

Delegated tasks run autonomously, like a personal agent, inside a bounded box:

- **Claude Code `auto` mode** (`permission_mode` in `config.toml`): routine actions go ahead, and a
  classifier blocks escalation and actions steered by hostile content.
- **Claude Code's built-in sandbox** (needs `bubblewrap` and `socat`; it refuses to start without
  them): shell commands run without prompts but can only write inside the task folder and reach
  approved network domains. It fails closed and the agent cannot retry a command outside it.
- **Secrets walled off** in both the sandbox and the file-tool permission rules: `~/.ssh`, `~/.aws`,
  `~/.gnupg`, `~/.config/gh`, `~/.kube`, credential files, this app's `.env`, its token and database.
  Deny rules hold in every permission mode.
- **Each task gets its own folder**: the one you name (inside your home directory only) or a private
  default workspace at `~/.voice-instinct/workspace/`.
- **Anything that still needs a human reaches you by voice.** A permission request goes through a
  hook; your spoken yes or no is decided by code from your own words, never by the LLM, and only if
  the assistant actually asked you. A question or a login code request from the agent arrives as
  its answer, and your reply resumes the same session.

The sandbox covers shell commands only. File tools, web fetching and MCP/browser tools run outside
it, which is why the secret deny rules exist and why browsing should use a dedicated profile.
`backend_cwd` defaults to your home directory; point it at a narrower directory for a tighter boundary.

The hook server listens on localhost only and requires a random token. The token, per-run settings
files and the database are private to your user (mode 600/700). The database stores every hook
payload, including commands and file contents Claude proposed to write.

Using a subscription CLI this way depends on the vendor's terms; check them before relying on it.

## Harness bridge

Delegated tasks run as tracked Claude Code sessions. Claude's permission requests reach you by
voice through a per-run hook (`--settings`, your global Claude settings are untouched): the
assistant asks, your spoken yes or no (decided by code, never the LLM) unblocks that one call in
the same run. Sessions, tasks, hook events and questions live in `~/.voice-instinct/harness.db`
(SQLite). Ask "what's Claude working on?" to list them. Hooks fail open: if this app is not
running, Claude is unaffected.

## Tests

`uv run pytest` runs the unit tests. `uv run pytest -m claude` runs the real-CLI integration tests
(permission hook allow/deny, fail-open, sandbox and secret rules), which need the `claude` CLI logged in
and use your subscription.
