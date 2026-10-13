// Pure orb state machine: RTVI events and server messages in, what to show out. Unit-tested.
function initial() {
  return { visible: false, mode: "hidden", connected: false };
}

function reduce(state, event) {
  const shown = (mode) => ({ ...state, visible: true, mode });
  switch (event.type) {
    case "connected":
      return { ...state, connected: true };
    case "disconnected":
      return { visible: false, mode: "hidden", connected: false };
    case "wake":
      return shown("listening");
    case "sleep":
      return { ...state, visible: false, mode: "hidden" };
    case "toggle":
      if (state.visible) return { ...state, visible: false, mode: "hidden" };
      return shown(state.connected ? "listening" : "offline");
    case "user-start":
      return state.visible ? shown("listening") : state;
    case "user-stop":
      return state.visible ? shown("thinking") : state;
    case "bot-start":
      return shown("speaking");
    case "bot-stop":
      return state.visible ? shown("listening") : state;
    default:
      return state;
  }
}

// What the tray says: the connection, and a microphone error that a later "connected" must not hide.
// backendUp: the backend answered after a failed connect, so the problem is on this side.
function statusLine(connected, micError, backendUp = false) {
  if (!connected && micError) return `Not connected: the microphone is unavailable (${micError})`;
  if (!connected && backendUp) return "Not connected: the backend is running, so check the microphone";
  if (!connected) return "Not connected (is the backend running?)";
  return micError ? `Connected, but the microphone is unavailable (${micError})` : "Connected";
}

module.exports = { initial, reduce, statusLine };
