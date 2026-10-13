# voice-instinct

A voice-first personal agent that runs on your own machine and handles everyday errands, in the
spirit of [Instinct](https://techcrunch.com/2026/09/28/viral-ai-agent-instinct-raises-1b-series-c-at-a-10b-valuation/),
the texting agent. You talk to it, it does the legwork, and it asks you by voice only when it needs a
decision, a choice or a login code. Local and private, open source, bring your own model.
Not affiliated with Instinct.

## What it does

- It lives in the background. Say "Hey Nova" (configurable) and a floating orb appears, listens, thinks
  and speaks, then hides again.
- You talk to it in the browser. Speech-to-text (Parakeet) and text-to-speech (Kokoro) run locally,
  around a model of your choice: Gemini by default, or any OpenAI-compatible endpoint or Ollama.
- It hands errands to your own logged-in `claude` CLI as a tracked background task, runs it
  autonomously in a sandbox, and tells you the result.
- When the task needs a go-ahead or an answer, it asks you aloud and carries on in the same run.
- It does errands only: research, bookings, shopping, bills, subscriptions, reminders and calling
  businesses. It declines coding and general computer tasks.

## How it works

```
mic -> Parakeet STT -> Smart Turn v3 -> front LLM -> Kokoro TTS -> speaker
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
- Your secrets (`~/.ssh`, cloud credentials, this app's keys and database) are off limits to the agent.
- Your yes or no is decided by code from your own words, never by the model, and only after it
  actually asked you.
- It is instructed never to buy, pay, book or cancel without your explicit go-ahead.
- Speech recognition and synthesis run locally, and sessions and questions are logged only on your
  machine in `~/.voice-instinct/`, readable only by you. The models you choose see the conversation
  and the task (Gemini and Claude are cloud services); use Ollama for a local front model.

Using a subscription CLI this way depends on the vendor's terms; check them before relying on it.

## License

Copyright (C) 2026 Deepak Kumar Soni. Licensed under the [GNU AGPL v3.0](LICENSE). You may use, change and share it, but if you distribute it, or let
people use a modified version over a network, you must give them its full source under the same
license.
