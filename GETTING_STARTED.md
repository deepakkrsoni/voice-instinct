# Getting started

Check [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) first: Linux, about 8 GB of RAM and 3.5 GB of disk.

1. **Install Claude Code and log in.** Follow https://code.claude.com/docs, then run `claude` once in
   a terminal and sign in. Errands run on this login.
2. **Get a Gemini API key** from https://aistudio.google.com/apikey (or plan to use Ollama, see below).
3. **Install voice-instinct:**
   ```
   git clone https://github.com/deepakkrsoni/voice-instinct && cd voice-instinct && ./install.sh
   ```
   It asks before using `sudo`, then asks for your `GEMINI_API_KEY` (stored in `.env`, readable only
   by you). It starts the backend as a background service and the orb, and both start by themselves
   at every login. `./install.sh --dry-run` shows every step without changing anything.
4. **On Sway or Hyprland**, add the lines the installer prints (details in
   [docs/sway.md](docs/sway.md)): a window rule, otherwise the orb opens in the middle of the screen,
   and a line that starts it at login, since these desktops skip the usual autostart.
5. **Say "Hey Nova".** The orb appears and listens. Ask for something, for example "find flights
   from Bangalore to Goa next Friday". It hides again after a few seconds of quiet.

## Everyday use

The commands below live in `~/.local/bin`; use the full path if that folder is not on your `PATH`.

- `voice-instinct toggle` shows or hides the orb (bind it to a key; Sway example in docs/sway.md).
- The tray menu shows the connection status, mutes the microphone and quits.
- `voice-instinct quit` quits the orb on desktops without a tray.
- Settings live in `config.toml` in the repo: the assistant's name, how long it stays awake, the
  model, the orb's size and position. After a change, restart both (see "If it doesn't respond").

## Use a local model instead of Gemini

In `config.toml`, set `provider = "ollama"` and `model` to a model you have pulled (for example
`qwen3:8b`). No API key is needed. Then restart both.

## Speech-to-text engine

Speech is transcribed on your machine by NVIDIA's Nemotron streaming model (English) by default. To
use the 40-language Nemotron or the previous Parakeet engine, set `engine` under `[stt]` in
`config.toml` to `nemotron-multilingual` or `parakeet`, then re-run `./install.sh` (it downloads only
the chosen model, about 450 MB) and restart. Nemotron's weights are under the NVIDIA Open Model License.

## Debug mode: see every step

To check what the agent does, set `browser_steps = true` under `[debug]` in `config.toml` and
restart (`systemctl --user restart voice-instinct`). Every step of an errand is then written to
`steps.md` in the errand's folder, under `debug/`: each web search with its query, each page it
fetches or opens, each command, and how the errand ended. Browser steps that change the page (open
a page, click, type, submit) also get a screenshot. What the agent typed or wrote is never written
down, but the address of every page it opens is, including any codes in a link it follows.

To also get each step and screenshot in Telegram, create a bot with @BotFather, send it a message, and put
its token and your chat id in `.env`:

    TELEGRAM_BOT_TOKEN=123456:ABC...
    TELEGRAM_CHAT_ID=123456789

They go only to that one chat, and only in debug mode.

## If it doesn't respond

- Check the tray status line: "Not connected" means the backend is down or still starting;
  "microphone unavailable" means the orb cannot open your mic.
- Backend log: `journalctl --user -u voice-instinct -n 50`. Orb log: `~/.voice-instinct/frontend.log`.
- Restart both: `systemctl --user restart voice-instinct`, then `voice-instinct quit` and `voice-instinct`.
- Errands need Claude Code: if it says Claude Code isn't set up, run `claude` and log in, then restart.

## Update or remove

- Update: `git pull`, then `./install.sh` again (it keeps your settings and downloads).
- Remove: `./install.sh --uninstall`; add `--purge` to also delete `~/.voice-instinct` (models,
  database, workspace).
