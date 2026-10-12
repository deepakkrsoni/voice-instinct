# voice-instinct

A voice-first assistant that runs on your own machine. You talk to it; a fast "front" model handles
the conversation and hands real tasks (files, shell, web, code) to a backend agent, then speaks
the result. Not affiliated with Instinct.

```
mic -> Parakeet STT -> Smart Turn v3 -> front LLM -> Kokoro TTS -> speaker
                                          |  ^
                                 delegate(task)  result
                                          v  |
                              Claude Code CLI (your login)
```

## Run

```
uv sync
echo 'GEMINI_API_KEY=...' > .env      # or whatever api_key_env names in config.toml
uv run bot.py                         # then open http://localhost:7860/client
```

Needs: Parakeet TDT 0.6B v3 (sherpa-onnx int8) in `~/.local/share/parakeet-dictation/models/desktop/`,
Kokoro (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) in `~/kokoro/`, and the `claude` CLI logged in.
Paths are hardcoded at the top of `bot.py` for now.

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
(allow, deny, server-down), which need the `claude` CLI logged in.
