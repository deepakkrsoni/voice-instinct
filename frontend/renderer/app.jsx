// Renderer: a Pipecat JS client over SmallWebRTC (documented path) driving the orb.
import { PipecatClient, RTVIEvent } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport, WavMediaManager } from "@pipecat-ai/small-webrtc-transport";
import { createRoot } from "react-dom/client";
import { Orb } from "./vendor/orb";

const { initial, reduce, agentStateFor } = require("./state.js");
const { makeReconnector } = require("./reconnect.js");

const backend = new URLSearchParams(location.search).get("backend") || "http://127.0.0.1:7860";
let state = initial();
const levels = { input: 0, output: 0 }; // read by the orb every frame, no re-render needed
const root = createRoot(document.getElementById("root"));
function render() {
  root.render(
    <Orb
      agentState={agentStateFor(state.mode)}
      volumeMode="manual"
      getInputVolume={() => levels.input}
      getOutputVolume={() => levels.output}
      colors={["#8B5CF6", "#22D3EE"]}
    />
  );
}
let muted = false; // owned by the tray in main.js; re-applied after every connect

function dispatch(type) {
  const before = state;
  state = reduce(state, { type });
  render();
  if (state.visible !== before.visible) window.vi.setVisible(state.visible);
}

const client = new PipecatClient({ transport: new SmallWebRTCTransport({ mediaManager: new WavMediaManager() }), enableMic: true, enableCam: false });
client.on(RTVIEvent.Connected, () => { dispatch("connected"); client.enableMic(!muted); });
client.on(RTVIEvent.Disconnected, () => { dispatch("disconnected"); reconnector.trigger(); });
client.on(RTVIEvent.ServerMessage, (msg) => { if (msg && (msg.type === "wake" || msg.type === "sleep")) dispatch(msg.type); });
client.on(RTVIEvent.UserStartedSpeaking, () => dispatch("user-start"));
client.on(RTVIEvent.UserStoppedSpeaking, () => dispatch("user-stop"));
client.on(RTVIEvent.BotStartedSpeaking, () => dispatch("bot-start"));
client.on(RTVIEvent.BotStoppedSpeaking, () => dispatch("bot-stop"));
client.on(RTVIEvent.LocalAudioLevel, (level) => { levels.input = level; });
client.on(RTVIEvent.RemoteAudioLevel, (level) => { levels.output = level; });

window.vi.onToggle(() => dispatch("toggle"));
window.vi.onMute((m) => { muted = m; client.enableMic(!muted); });

const reconnector = makeReconnector(async () => {
  client.enableMic(!muted); // mic follows mute before any audio can flow
  await client.connect({ webrtcRequestParams: { endpoint: `${backend}/api/offer` } });
  reconnector.succeeded();
});
reconnector.trigger();
render();
