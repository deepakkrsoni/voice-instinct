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
| `backend_cwd`, `backend_timeout` | where the backend agent works (default home) and its timeout (120 s) |

## Safety

The backend starts read-only (`Read/Glob/Grep` and a few `Bash(ls|du|find|stat|wc|cat)` commands).
Anything else is blocked, the assistant tells you what it wants to do and asks yes/no, and only an
explicit spoken "yes" (decided by code on your actual words, not by the LLM) re-runs it. Approving
allows the whole blocked tool (e.g. `Bash`) for that one retry.

Using a subscription CLI this way depends on the vendor's terms; check them before relying on it.

## Tests

`uv run pytest`
