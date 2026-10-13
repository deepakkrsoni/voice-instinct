// Tray menu items, pure so it can be tested. Labels say what a click does, because some tray hosts
// (waybar on Sway) do not draw checkmarks.
function trayItems({ status, muted }, { toggle, setMuted, quit }) {
  return [
    { label: muted ? `${status}, microphone muted` : status, enabled: false },
    { label: "Show / Hide", click: toggle },
    { label: muted ? "Unmute microphone" : "Mute microphone", click: () => setMuted(!muted) },
    { type: "separator" },
    { label: "Quit", click: quit },
  ];
}

module.exports = { trayItems };
