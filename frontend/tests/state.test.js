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

const { agentStateFor } = require("../renderer/state.js");

test("orb state follows the mode (speaking is the orb's 'talking')", () => {
  assert.equal(agentStateFor("listening"), "listening");
  assert.equal(agentStateFor("thinking"), "thinking");
  assert.equal(agentStateFor("speaking"), "talking");
  assert.equal(agentStateFor("hidden"), null);
  assert.equal(agentStateFor("anything-else"), null);
});
