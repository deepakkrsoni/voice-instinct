# voice-instinct PoC: Design

Working name `voice-instinct` (unaffiliated with Instinct; the name can change later).

## Goal

An open-source, voice-first "Jarvis" that runs on the user's own machine. You talk to it, it does
real tasks, and it speaks the result. The user brings the brain: an API key (or local Ollama) for
conversation, and optionally their own logged-in Claude Code CLI for doing work. We run no
servers, accounts, or billing.

## PoC success criterion

Say "what's in my downloads folder and which file is biggest?" and hear the correct answer, with
the conversational voice loop still feeling fast (existing pipeline: Parakeet, Smart Turn v3, Kokoro).

## Scope

In: local-only, single user; a two-layer brain; one backend adapter (Claude Code CLI); a config
file for the front model; a confirmation step before risky actions.

Out (later): Codex adapter, remote access (phone to home machine), memory, proactive behavior,
auth, any UI beyond Pipecat's test page.

## Architecture

```
mic -> Parakeet STT -> Smart Turn v3 -> FRONT LLM -> Kokoro TTS -> speaker
                                           |  ^
                                  delegate(task)  result/progress text
                                           v  |
                                     BACKEND adapter
                                  (claude -p, stream-json)
```

- **Front LLM:** fast, handles live conversation. Provider is config: Gemini key, any
  OpenAI-compatible endpoint, or Ollama. It has one tool, `delegate(task)`, and decides when a
  request needs real work.
- **Backend adapter:** an interface `run(task, cwd) -> stream of events` (progress text, final
  result, needs-confirmation). PoC implements `ClaudeCodeBackend`, which spawns
  `claude -p --output-format stream-json` as a subprocess. Codex or "front model only" are
  future implementations of the same interface.
- **Frame of reference:** this is the frontend-backend delegation design from
  arXiv 2609.19334, chosen so the voice front stays fast while the heavy agent runs behind it.

## Data flow for a delegated task

1. User speaks; the front LLM calls `delegate(task)`.
2. The front speaks a short acknowledgement ("on it") immediately.
3. The adapter runs the CLI, streaming events. Optional short progress lines may be spoken.
4. The final result text is returned to the front as the tool result; the front summarizes it
   aloud in one to three sentences.
5. Follow-up turns reuse the CLI session (`--resume`) so context carries over.

## Safety

The backend starts in a read-only/plan permission mode. Any action needing write or shell access
produces a `needs-confirmation` event; the front asks aloud ("I'd like to run X, okay?") and only
proceeds on an explicit spoken yes, by re-running with broader permission for that task. Default
working directory is the user's home, configurable.

## Error handling

CLI not found or not logged in: say so aloud once and fall back to front-model-only. Backend
timeout (configurable, default 120 s): speak that it's still working or gave up. Front model API
errors: spoken short apology, no crash. Barge-in during a delegated task cancels speech, not the
task.

## Testing

- Unit: the adapter's stream-json parser against recorded fixture output.
- Unit: the confirmation state machine (confirm, deny, ambiguous).
- Smoke: the existing script's STT and TTS round trip.
- Manual: the downloads-folder demo end to end.

## Open items

- Read the vendors' terms on driving subscription CLIs from a third-party app before promoting
  that as a feature. The design only spawns the user's own installed CLI.
- Codex headless mode is unverified (CLI not installed on the dev machine).
- Parakeet currently transcribes after speech ends (offline model); a streaming model is a later
  latency upgrade.
