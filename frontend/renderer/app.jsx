// Renderer: a Pipecat JS client over SmallWebRTC (documented path) driving the orb.
import { PipecatClient, RTVIEvent } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport, WavMediaManager } from "@pipecat-ai/small-webrtc-transport";
import { createRoot } from "react-dom/client";
import { Orb } from "orb-ui";
import { createPipecatAdapter } from "orb-ui/adapters";

const { initial, reduce, statusFor } = require("./state.js");
const { makeReconnector } = require("./reconnect.js");

const backend = new URLSearchParams(location.search).get("backend") || "http://127.0.0.1:7860";
let state = initial();
const root = createRoot(document.getElementById("root"));
let muted = false; // owned by the tray in main.js; re-applied after every connect

function dispatch(type, extra) {
  const before = state;
  state = reduce(state, { type });
  if (state.visible !== before.visible) window.vi.setVisible(state.visible);
  const status = statusFor({ type, ...extra });
  if (status) window.vi.setStatus(status);
}

const client = new PipecatClient({ transport: new SmallWebRTCTransport({ mediaManager: new WavMediaManager() }), enableMic: true, enableCam: false });
client.on(RTVIEvent.Connected, () => { dispatch("connected"); client.enableMic(!muted); });
client.on(RTVIEvent.BotReady, () => { if (state.visible) client.sendClientMessage("wake"); }); // shown before it connected
client.on(RTVIEvent.Disconnected, () => { dispatch("disconnected"); reconnector.trigger(); });
client.on(RTVIEvent.ServerMessage, (msg) => { if (msg && (msg.type === "wake" || msg.type === "sleep")) dispatch(msg.type); });
client.on(RTVIEvent.DeviceError, (err) => dispatch("device-error", { reason: err?.type || "unknown" }));
client.on(RTVIEvent.UserStartedSpeaking, () => dispatch("user-start"));
client.on(RTVIEvent.UserStoppedSpeaking, () => dispatch("user-stop"));
client.on(RTVIEvent.BotStartedSpeaking, () => dispatch("bot-start"));
client.on(RTVIEvent.BotStoppedSpeaking, () => dispatch("bot-stop"));

window.vi.onToggle(() => {
  dispatch("toggle");
  if (state.connected) client.sendClientMessage(state.visible ? "wake" : "sleep"); // by hand: listen, or stop
});
window.vi.onMute((m) => { muted = m; client.enableMic(!muted); });

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
  await adapter.start();
  reconnector.succeeded();
}, setTimeout, { connecting: () => LIVE.includes(client.state) });
reconnector.trigger();
// The wrapper is the drag handle and carries the hover effect (index.html); 80vmin leaves room for both.
root.render(
  <div className="orb-wrap">
    <Orb adapter={adapter} theme="cloud" interactive={false} aria-label="voice-instinct" style={{ "--orb-ui-size": "80vmin" }} />
  </div>
);
