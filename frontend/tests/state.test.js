const test = require("node:test");
const assert = require("node:assert/strict");
const { initial, reduce } = require("../renderer/state.js");

const run = (...events) => events.reduce((s, e) => reduce(s, { type: e }), initial());

test("starts hidden and wakes on the server's wake message", () => {
  assert.deepEqual(initial(), { visible: false, mode: "hidden", connected: false });
  assert.deepEqual(run("connected", "wake"), { visible: true, mode: "listening", connected: true });
});

test("follows the conversation: user speaks, bot thinks, bot speaks, back to listening", () => {
  assert.equal(run("wake", "user-start").mode, "listening");
  assert.equal(run("wake", "user-start", "user-stop").mode, "thinking");
  assert.equal(run("wake", "user-stop", "bot-start").mode, "speaking");
  assert.equal(run("wake", "bot-start", "bot-stop").mode, "listening");
});

test("pre-wake speech does not show the orb", () => {
  assert.equal(run("connected", "user-start", "user-stop").visible, false);
});

test("sleep hides it; toggle shows and hides it", () => {
  assert.equal(run("wake", "sleep").visible, false);
  assert.equal(run("toggle").visible, true);
  assert.equal(run("toggle", "toggle").visible, false);
});

test("losing the backend hides the orb instead of freezing it", () => {
  assert.deepEqual(run("connected", "wake", "disconnected"), { visible: false, mode: "hidden", connected: false });
});

test("unknown events change nothing", () => {
  assert.deepEqual(run("wake", "nonsense"), run("wake"));
});

const { statusLine } = require("../renderer/state.js");

test("the tray says whether it is connected and keeps saying why the microphone failed", () => {
  assert.equal(statusLine(true, null), "Connected");
  assert.equal(statusLine(false, null), "Not connected (is the backend running?)");
  // The mic error arrives before Connected (the transport connects anyway): it must not be overwritten.
  assert.equal(statusLine(true, "permissions"), "Connected, but the microphone is unavailable (permissions)");
  assert.equal(statusLine(false, "not-found"), "Not connected: the microphone is unavailable (not-found)");
});

test("showing the orb while disconnected marks it offline (the app hides it again shortly)", () => {
  const s = reduce(initial(), { type: "toggle" });
  assert.equal(s.visible, true);
  assert.equal(s.mode, "offline");
  const c = reduce(reduce(initial(), { type: "connected" }), { type: "toggle" });
  assert.equal(c.mode, "listening");
});

test("when the backend is up but the session will not connect, the tray points at the microphone", () => {
  // Without a usable mic the client fails before it reaches the backend, and no device error arrives.
  assert.equal(statusLine(false, null, true), "Not connected: the backend is running, so check the microphone");
  assert.equal(statusLine(false, "permissions", true), "Not connected: the microphone is unavailable (permissions)");
  assert.equal(statusLine(false, null, false), "Not connected (is the backend running?)");
});
