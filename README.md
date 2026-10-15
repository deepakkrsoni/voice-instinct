# voice-instinct

A voice-first personal agent that runs on your own machine and handles everyday errands, in the
spirit of [Instinct](https://techcrunch.com/2026/09/28/viral-ai-agent-instinct-raises-1b-series-c-at-a-10b-valuation/),
the texting agent. You talk to it, it does the legwork, and it asks you by voice only when it needs a
decision or a choice. Local and private, open source, bring your own model.
Not affiliated with Instinct.

## What it does

- It lives in the background. Say "Nova" (the name is configurable) and a floating orb appears, listens, thinks
  and speaks, then hides again.
- You just talk to it. Speech-to-text (Nemotron streaming, or Parakeet) and text-to-speech (Kokoro) run locally,
  around a model of your choice: Gemini by default, or any OpenAI-compatible endpoint or Ollama.
- It hands errands to your own logged-in `claude` CLI as a tracked background task, runs it
  autonomously in a sandbox, and tells you the result.
- When the task needs a go-ahead or an answer, it asks you aloud and carries on in the same run.
- It is for errands, not coding or general computer tasks. Today that means research done on the web:
  finding and comparing flights, products, plans and prices, and answering questions about them.
- It is a Linux desktop app. The tray menu needs a desktop with a system tray (on GNOME, the
  AppIndicator extension); see [docs/sway.md](docs/sway.md) for Sway.

To set it up, see [GETTING_STARTED.md](GETTING_STARTED.md).

## How it works

```
mic -> Nemotron STT -> Smart Turn v3 -> front LLM -> Kokoro TTS -> speaker
                                          |  ^
                                 delegate(task)  result / questions
                                          v  |
                  Claude Code CLI (your login) in a sandboxed task folder
                          hooks -> local relay -> asks you by voice
```

Built on [Pipecat](https://docs.pipecat.ai).

## How it keeps you in control

- Shell commands run inside Claude Code's sandbox, can only write inside the task's own folder, and
  fail closed if the sandbox is unavailable.
- Your secrets (`~/.ssh`, cloud credentials, this app's keys and database) are off limits to the agent,
  and the front model's API key is removed from the agent's environment.
- The agent browses in its own headless browser that starts empty for every errand and is never
  signed in to your accounts. It gets none of your other Claude Code tools or connectors.
- When Claude Code asks for permission, your yes or no is decided by code from your own words, never
  by the model, and only after it actually asked you. Clicks and form entries in the errand browser
  are not asked one by one: Claude Code's auto-mode safety checks approve or refuse them, and the
  browser's tools that could run code or upload your files are turned off.
- It is instructed never to buy, pay, book or cancel without your explicit go-ahead.
- The microphone stays open so it can hear the wake phrase. What it hears is transcribed on your
  machine and thrown away until you say the phrase; it is not stored or sent anywhere.
- Speech recognition and synthesis run locally, and sessions and questions are logged only on your
  machine in `~/.voice-instinct/`, readable only by you. The models you choose see the conversation
  and the task (Gemini and Claude are cloud services); use Ollama for a local front model.
- Only your voice wakes it, once you enrol: with the orb open, run `cd backend && uv run enroll.py` and
  read five lines to the orb (one minute). It works at once. As with Siri, the voice check is on the wake
  phrase: other people saying "Nova" are ignored, and once you've woken it, it listens to the conversation
  until it sleeps. The voiceprint stays in `~/.voice-instinct/`, and `uv run enroll.py --delete` removes it.
  If it misses you, lower `[voice] threshold` in `config.toml` (default 0.6); if others get through, raise
  it. It needs the Parakeet engine. Say "stop" to send it away; running errands carry on.

Using a subscription CLI this way depends on the vendor's terms; check them before relying on it.

## License

Copyright (C) 2026 Deepak Kumar Soni. Licensed under the [GNU AGPL v3.0](LICENSE). You may use, change and share it, but if you distribute it, or let
people use a modified version over a network, you must give them its full source under the same
license.
