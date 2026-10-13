// Renderer: a Pipecat JS client over SmallWebRTC (documented path) driving the orb.
import { PipecatClient, RTVIEvent } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport, WavMediaManager } from "@pipecat-ai/small-webrtc-transport";
import { createRoot } from "react-dom/client";
import { Orb } from "orb-ui";
import { createPipecatAdapter } from "orb-ui/adapters";

const { initial, reduce, statusLine } = require("./state.js");
const { makeReconnector, makeWatchdog } = require("./reconnect.js");

const backend = new URLSearchParams(location.search).get("backend") || "http://127.0.0.1:7860";
let state = initial();
const root = createRoot(document.getElementById("root"));
let muted = false; // owned by the tray in main.js; re-applied after every connect

let micError = null; // set by a device error, cleared once our mic track really starts
let backendUp = false; // after a failed connect: does the backend itself answer?
let lastLine = null;
function dispatch(type) {
  const before = state;
  state = reduce(state, { type });
  if (state.visible !== before.visible) window.vi.setVisible(state.visible);
  const line = statusLine(state.connected, micError, backendUp);
  if (line !== lastLine) window.vi.setStatus((lastLine = line)); // a rebuilt tray menu closes if open
}

const client = new PipecatClient({ transport: new SmallWebRTCTransport({ mediaManager: new WavMediaManager() }), enableMic: true, enableCam: false });
client.on(RTVIEvent.Connected, () => { backendUp = false; dispatch("connected"); if (client.isMicEnabled !== !muted) client.enableMic(!muted); });
client.on(RTVIEvent.BotReady, () => { if (state.visible) client.sendClientMessage("wake"); }); // shown before it connected
client.on(RTVIEvent.Disconnected, () => { dispatch("disconnected"); reconnector.trigger(); });
client.on(RTVIEvent.ServerMessage, (msg) => { if (msg && (msg.type === "wake" || msg.type === "sleep")) dispatch(msg.type); });
client.on(RTVIEvent.DeviceError, (err) => { micError = err?.type || "unknown"; dispatch("device-error"); });
client.on(RTVIEvent.TrackStarted, (track, participant) => {
  if (participant?.local && track.kind === "audio" && micError) { micError = null; dispatch("mic-ok"); }
});
// A stalled session (media stopped without a Disconnected) is torn down so the reconnector takes over.
// disconnect() emits no Disconnected when the peer is already gone, so trigger the reconnector here.
const watchdog = makeWatchdog(() => {
  console.warn("session stalled; reconnecting");
  client.disconnect().catch(() => {}).then(() => reconnector.trigger());
});
client.on(RTVIEvent.TransportStateChanged, (s) => watchdog.state(s));
client.on(RTVIEvent.UserStartedSpeaking, () => dispatch("user-start"));
client.on(RTVIEvent.UserStoppedSpeaking, () => dispatch("user-stop"));
client.on(RTVIEvent.BotStartedSpeaking, () => dispatch("bot-start"));
client.on(RTVIEvent.BotStoppedSpeaking, () => dispatch("bot-stop"));

window.vi.onToggle(() => {
  dispatch("toggle");
  if (state.mode === "offline") setTimeout(() => { if (state.mode === "offline") dispatch("sleep"); }, 4000); // tray says why
  if (state.connected) client.sendClientMessage(state.visible ? "wake" : "sleep"); // by hand: listen, or stop
});
window.vi.onMute((m) => {
  muted = m;
  render(); // a muted orb must not look like it listens
  if (client.isMicEnabled !== !muted) client.enableMic(!muted);
});
window.vi.setVisible(false); // a reloaded page starts hidden, so the window must too

// The adapter drives the orb's look from RTVI events; our code owns connect/retry. adapter.start()
// only marks "connecting" and calls our connect (it never reconnects by itself), so the reconnector
// stays the single caller. The adapter plays the bot's audio (nothing else does: the transport only
// plays the WebRTC remote track if an element is attached, and WavMediaManager's player is fed by
// bufferBotAudio, which SmallWebRTC never calls).
const botAudio = new Audio(); // one element: a repeated trackStarted replaces the source, never adds a player
const adapter = createPipecatAdapter(client, {
  createAudioElement: () => botAudio,
  connect: async () => {
    client.enableMic(!muted); // mic follows mute before any audio can flow
    await client.connect({ webrtcRequestParams: { endpoint: `${backend}/api/offer` } });
  },
});
const LIVE = ["authenticating", "connecting", "connected", "ready"]; // client-js refuses connect() in these
const reconnector = makeReconnector(async () => {
  try {
    await adapter.start();
  } catch (e) {
    backendUp = await fetch(`${backend}/gui-config`).then((r) => r.ok, () => false);
    dispatch("connect-failed");
    throw e;
  }
  reconnector.succeeded();
}, setTimeout, { connecting: () => LIVE.includes(client.state) });
reconnector.trigger();
// The wrapper is the drag handle and carries the hover effect (index.html); 80vmin leaves room for both.
function render() {
  root.render(
    <div className={muted ? "orb-wrap muted" : "orb-wrap"}>
      <Orb adapter={adapter} theme="cloud" interactive={false} aria-label="voice-instinct" style={{ "--orb-ui-size": "80vmin" }} />
    </div>
  );
}
render();
