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
      return state.visible ? { ...state, visible: false, mode: "hidden" } : shown("listening");
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

module.exports = { initial, reduce };
