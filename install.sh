#!/usr/bin/env bash
# voice-instinct installer: system packages, uv, models, backend, frontend, services.
# Usage: ./install.sh [--dry-run] [--uninstall] [--purge]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${VI_HOME:-$HOME}"
OS_RELEASE="${VI_OS_RELEASE:-/etc/os-release}"
DRY=0 UNINSTALL=0 PURGE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --purge) PURGE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PKGS=(bubblewrap socat curl tar bzip2)
NODE_VER="22.23.3"  # one pinned official Node: distro Node (Debian 12 has 18) is too old for Electron 44
NODE_DIR="$HOME_DIR/.voice-instinct/node"
case "$(uname -m)" in
  x86_64) NODE_ARCH=x64; NODE_SHA="df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de" ;;
  aarch64) NODE_ARCH=arm64; NODE_SHA="a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f" ;;
  *) NODE_ARCH="" NODE_SHA="" ;;
esac
MODELS="$HOME_DIR/.voice-instinct/models"
PARAKEET="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PARAKEET_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$PARAKEET.tar.bz2"
PARAKEET_SHA="5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf"
KOKORO_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_ONNX_SHA="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
KOKORO_VOICES_SHA="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
UNIT="$HOME_DIR/.config/systemd/user/voice-instinct.service"
AUTOSTART="$HOME_DIR/.config/autostart/voice-instinct.desktop"
LAUNCHER="$HOME_DIR/.local/bin/voice-instinct"

say() { printf '==> %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then printf 'would run: %s\n' "$*"; else "$@"; fi; }
write() { # write <path> <mode>  (content on stdin)
  if [ "$DRY" = 1 ]; then printf 'would write: %s\n' "$1"; cat >/dev/null; return; fi
  mkdir -p "$(dirname "$1")"; cat >"$1"; chmod "$2" "$1"
}

if [ "$(id -u)" = 0 ] && [ "$DRY" = 0 ]; then echo "Run this as your normal user, not root (it uses sudo only for packages)." >&2; exit 1; fi

distro_install() {
  # shellcheck disable=SC1090
  . "$OS_RELEASE"
  local family="${ID:-} ${ID_LIKE:-}"
  case "$family" in
    *arch*) PKGS+=(xz); PM=(sudo pacman -S --needed "${PKGS[@]}") ;;
    *debian*|*ubuntu*) PKGS+=(xz-utils); PM=(sudo apt-get install -y "${PKGS[@]}") ;;
    *fedora*|*rhel*) PKGS+=(xz); PM=(sudo dnf install -y "${PKGS[@]}") ;;
    *) echo "Your distribution (${NAME:-unknown}) is not supported yet. Supported: Arch, Debian/Ubuntu, Fedora." >&2
       echo "Install these yourself, then re-run: ${PKGS[*]}" >&2; exit 1 ;;
  esac
  say "System packages (needs sudo): ${PKGS[*]}"
  if [ "$DRY" = 0 ]; then
    read -r -p "Install them now with sudo? [Y/n] " ok || ok=y
    case "${ok:-y}" in [nN]*) echo "Skipped. Install them yourself, then re-run." >&2; exit 1 ;; esac
  fi
  run "${PM[@]}"
}

fetch() { # fetch <url> <dest> <sha256>
  local url="$1" dest="$2" sha="$3"
  if [ -f "$dest" ] && [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$sha" ]; then say "keep $(basename "$dest") (checksum ok)"; return; fi
  run mkdir -p "$(dirname "$dest")"
  run curl -fL --retry 2 -o "$dest.part" "$url"
  if [ "$DRY" = 0 ]; then
    if [ "$(sha256sum "$dest.part" | cut -d' ' -f1)" != "$sha" ]; then rm -f "$dest.part"; echo "Checksum mismatch for $url" >&2; exit 1; fi
    mv "$dest.part" "$dest"
  fi
}

node_install() {
  if [ "$("$NODE_DIR/bin/node" --version 2>/dev/null)" = "v$NODE_VER" ]; then say "keep Node v$NODE_VER"; return; fi
  local tgz="$HOME_DIR/.voice-instinct/node-v$NODE_VER-linux-$NODE_ARCH.tar.xz"
  fetch "https://nodejs.org/dist/v$NODE_VER/node-v$NODE_VER-linux-$NODE_ARCH.tar.xz" "$tgz" "$NODE_SHA"
  run rm -rf "$NODE_DIR"
  run mkdir -p "$NODE_DIR"
  run tar -xJf "$tgz" -C "$NODE_DIR" --strip-components=1
  run rm -f "$tgz"
}

models() {
  if [ -f "$MODELS/$PARAKEET/encoder.int8.onnx" ]; then say "keep Parakeet model"; else
    fetch "$PARAKEET_URL" "$MODELS/$PARAKEET.tar.bz2" "$PARAKEET_SHA"
    run tar -xjf "$MODELS/$PARAKEET.tar.bz2" -C "$MODELS"
    run rm -f "$MODELS/$PARAKEET.tar.bz2"
  fi
  fetch "$KOKORO_BASE/kokoro-v1.0.onnx" "$MODELS/kokoro/kokoro-v1.0.onnx" "$KOKORO_ONNX_SHA"
  fetch "$KOKORO_BASE/voices-v1.0.bin" "$MODELS/kokoro/voices-v1.0.bin" "$KOKORO_VOICES_SHA"
}

# cfgval <key>: value from config.toml, else config.example.toml; never fails (no pipefail abort on a missing file)
cfgval() {
  local f v=""
  for f in "$REPO/config.toml" "$REPO/config.example.toml"; do
    [ -f "$f" ] || continue
    v="$(sed -n -e "s/^$1 *= *\"\([^\"]*\)\".*/\1/p" -e "s/^$1 *= *\([0-9][0-9]*\).*/\1/p" "$f" | head -1)"
    [ -n "$v" ] && break
  done
  printf '%s' "$v"
}

append_env() { # append_env <file> <key> <value>
  if [ -s "$1" ] && [ -n "$(tail -c1 "$1")" ]; then printf '\n' >>"$1"; fi
  (umask 077; printf '%s=%s\n' "$2" "$3" >>"$1"); chmod 600 "$1"
}

config_and_key() {
  if [ -f "$REPO/config.toml" ]; then say "keep existing config.toml"; else
    say "create config.toml from config.example.toml"; run cp "$REPO/config.example.toml" "$REPO/config.toml"; fi
  local key; key="$(cfgval api_key_env)"
  if [ -n "$key" ] && ! grep -qs "^$key=" "$REPO/.env"; then
    if [ "$DRY" = 1 ] || [ ! -t 0 ]; then say "add $key=... to $REPO/.env (needed for the front model)"; else
      read -r -s -p "$key (input hidden; leave empty to add it later): " val; echo
      if [ -n "$val" ]; then append_env "$REPO/.env" "$key" "$val"; fi
    fi
  fi
}

sway_rule() {
  local size w="" rule="floating enable, sticky enable, border none"
  size="$(cfgval size)"; size="${size:-128}"
  if command -v swaymsg >/dev/null; then
    w="$(swaymsg -t get_outputs 2>/dev/null | python3 -c 'import json,sys; print(next(o["rect"]["width"] for o in json.load(sys.stdin) if o.get("focused")))' 2>/dev/null || true)"
  fi
  if [ -n "$w" ]; then
    rule="$rule, resize set $size $size, move position $((w - size - 24)) 12"
    say "Sway ignores app positioning; add to your config:  for_window [app_id=\"voice-instinct\"] $rule"
  else
    say "swaymsg unavailable, so no resize/move; add to your config:  for_window [app_id=\"voice-instinct\"] $rule"
  fi
  say "         and a key:        bindsym \$mod+n exec voice-instinct toggle"
  say "         (details: docs/sway.md)"
}

services() {
  local uvbin="$UV"
  write "$UNIT" 644 <<EOF
[Unit]
Description=voice-instinct backend

[Service]
WorkingDirectory=$REPO/backend
ExecStart=$uvbin run --project $REPO/backend python bot.py
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
  write "$LAUNCHER" 755 <<EOF
#!/usr/bin/env bash
# voice-instinct launcher: "voice-instinct" starts the orb, "voice-instinct toggle" shows or hides it.
args=()
[ "\${1:-}" = toggle ] && args=(--toggle)
exec "$REPO/frontend/node_modules/.bin/electron" "$REPO/frontend" "\${args[@]}"
EOF
  write "$AUTOSTART" 644 <<EOF
[Desktop Entry]
Type=Application
Name=voice-instinct
Exec=$LAUNCHER
X-GNOME-Autostart-enabled=true
EOF
  if [ "$DRY" = 1 ] || systemctl --user show-environment >/dev/null 2>&1; then
    run systemctl --user daemon-reload
    run systemctl --user enable voice-instinct.service
    run systemctl --user restart voice-instinct.service  # re-run = update: pick up new code
  else
    say "No systemd user session here; start the backend with: cd $REPO/backend && uv run bot.py"
  fi
  case "${XDG_CURRENT_DESKTOP:-}${SWAYSOCK:+sway}${HYPRLAND_INSTANCE_SIGNATURE:+hyprland}" in
    *sway*) sway_rule ;;
    *hyprland*|*Hyprland*) say "Hyprland: windowrulev2 = float, class:^(voice-instinct)\$ and windowrulev2 = pin, class:^(voice-instinct)\$" ;;
  esac
}

uninstall() {
  # idempotent: a missing unit or no user systemd session must not stop the cleanup
  if [ "$DRY" = 1 ]; then run systemctl --user disable --now voice-instinct.service
  else systemctl --user disable --now voice-instinct.service 2>/dev/null || true; fi
  run rm -f "$UNIT" "$AUTOSTART" "$LAUNCHER"
  if [ "$PURGE" = 1 ]; then say "remove data in $HOME_DIR/.voice-instinct (models, database, workspace)"; run rm -rf "$HOME_DIR/.voice-instinct"; fi
  say "voice-instinct removed$([ "$PURGE" = 1 ] || echo '; your data in ~/.voice-instinct is kept (use --purge to delete it)')"
}

if [ "$UNINSTALL" = 1 ]; then uninstall; exit 0; fi

if [ -z "$NODE_ARCH" ]; then echo "unsupported CPU: $(uname -m) (supported: x86_64, aarch64)" >&2; exit 1; fi
distro_install
node_install
command -v uv >/dev/null || run sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
UV="$(command -v uv || echo "$HOME_DIR/.local/bin/uv")"  # the fresh install is not on PATH yet
models
config_and_key
run sh -c "cd '$REPO/backend' && $UV sync --python 3.13"
# npm 12 blocks install scripts, so fetch the Electron binary explicitly (harmless where npm already did)
run sh -c "export PATH=\"$NODE_DIR/bin:\$PATH\"; cd '$REPO/frontend' && npm ci && node node_modules/electron/install.js && npm run build"
if ! command -v claude >/dev/null; then
  say "Install Claude Code (https://code.claude.com/docs) and log in with 'claude', then re-run this script."
fi
services
name="$(cfgval name)"
say "Done. Say \"Hey ${name:-Nova}\"."
