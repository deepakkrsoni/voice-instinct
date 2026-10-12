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
| `backend_cwd`, `backend_timeout` | where the backend agent works (default home) and its timeout (300 s; keep it well above `permission_timeout`, 90 s) |

## Safety

The backend pre-approves only read-only tools (`Read`, `Glob`, `Grep`, and `Bash` for `ls`, `du`,
`stat`, `wc`, `cat`). `find` is left out because it can delete. Anything else makes Claude Code ask
permission through a hook; the assistant tells you what it wants to do and asks yes or no.

- The answer is decided by code from your own words, never by the LLM, and an allow applies to that
  single tool call.
- A "yes" counts only if the question is in the conversation, the assistant actually asked it, and
  you replied after that. Claude's own text cannot forge that: it is shown to the LLM as quoted data.
- `backend_cwd` defaults to your home directory, and Claude Code's read-only handling treats paths
  inside it as in scope. Point it at a narrower directory if you want a tighter boundary.
- The hook server listens on localhost only and requires a random token. The token, the per-run
  settings files and the database are private to your user (mode 600/700). The database stores every
  hook payload, including commands and file contents Claude proposed to write.

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
