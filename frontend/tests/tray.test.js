const test = require("node:test");
const assert = require("node:assert/strict");
const { trayItems } = require("../tray.js");

// Some tray hosts (waybar on Sway) do not draw checkmarks, so the label itself must say what a click does.
test("the mic item says Unmute when muted and Mute when not, and a click flips it", () => {
  const calls = [];
  const actions = { toggle: () => calls.push("toggle"), setMuted: (m) => calls.push(m), quit: () => calls.push("quit") };
  const mic = (muted) => trayItems({ status: "Connected", muted }, actions).find((i) => /^(un)?mute microphone$/i.test(i.label || ""));
  assert.equal(mic(false).label, "Mute microphone");
  assert.equal(mic(true).label, "Unmute microphone");
  assert.equal(mic(false).type, undefined); // a plain item, not a checkbox
  mic(false).click();
  mic(true).click();
  assert.deepEqual(calls, [true, false]);
});

test("the first item is the status, and the status says when the mic is muted", () => {
  const items = (muted) => trayItems({ status: "Connected", muted }, { toggle() {}, setMuted() {}, quit() {} });
  assert.deepEqual(items(false)[0], { label: "Connected", enabled: false });
  assert.equal(items(true)[0].label, "Connected, microphone muted");
});
