# System requirements

## Computer

- **Operating system:** Linux. The installer supports Arch, Debian/Ubuntu and Fedora (and their
  derivatives). macOS and Windows are not supported yet.
- **CPU:** 64-bit x86 or ARM (`x86_64` or `aarch64`). Speech recognition and synthesis run on the
  CPU; no GPU is needed. A recent 4-core CPU or better is recommended (tested on a 12-core AMD Ryzen AI Max+ laptop).
- **Memory:** 8 GB minimum, 16 GB recommended. In use, the backend takes about 2 GB, the orb about
  0.5 GB, and a running errand (Claude Code and its browser) about 1 GB more.
- **Disk:** about 3.5 GB free. The installer downloads about 2 GB: speech models (~1 GB; Whisper is 0.1 to 0.6 GB), Node.js
  (~0.2 GB), the errand browser (~0.7 GB), plus the Python and Node packages.
- **Microphone and speakers** (or a headset).

## Desktop

- **Tested:** Sway (Wayland) with waybar. On Sway, add the window rule from
  [docs/sway.md](docs/sway.md) so the orb floats in the corner.
- **Expected to work, not yet tested:** Hyprland (the installer prints its rule), GNOME, KDE and X11
  desktops.
- **Tray menu:** needs a desktop with a system tray. On GNOME, install the AppIndicator extension;
  without a tray, quit with `voice-instinct quit`.

## Accounts and network

- **Claude Code**, installed and logged in (run `claude` once in a terminal). Errands run on your
  own Claude Code login.
- **A model for the voice:** a Gemini API key by default (the installer asks for it), or any
  OpenAI-compatible endpoint, or a local Ollama model (set `[model]` in `config.toml`).
- **Internet:** for the installer's downloads, the front model (unless you use Ollama), Claude Code
  and the web pages errands visit. Speech recognition and synthesis stay on your machine.

## Installed for you

`./install.sh` installs everything else and asks before using `sudo`:

- system packages `bubblewrap`, `socat`, `curl`, `tar`, `bzip2`, `xz` (Claude Code's sandbox needs
  `bubblewrap` and `socat`);
- [uv](https://docs.astral.sh/uv/) and Python 3.13 for the backend;
- a pinned Node.js 22 for the desktop app and the errand browser, kept in `~/.voice-instinct`
  (your system Node is not used or changed).
