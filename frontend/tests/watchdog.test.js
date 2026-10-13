const test = require("node:test");
const assert = require("node:assert/strict");
const { makeWatchdog } = require("../renderer/reconnect.js");

function setup() {
  const timers = [];
  let fired = 0;
  const w = makeWatchdog(() => fired++, (fn, ms) => { const t = { fn, ms, live: true }; timers.push(t); return t; },
    (t) => { t.live = false; }, 15000);
  return { w, timers, fired: () => fired };
}

// Live, the session went silent at 23:12 and the orb never recovered: nothing noticed the stall.
test("a session that leaves ready and does not come back is restarted after 15 s", () => {
  const { w, timers, fired } = setup();
  w.state("ready");
  w.state("disconnecting");
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 15000);
  timers[0].fn();
  assert.equal(fired(), 1);
});

test("coming back to ready in time cancels it, and repeated bad states arm only one timer", () => {
  const { w, timers, fired } = setup();
  w.state("ready");
  w.state("connecting");
  w.state("connected");
  assert.equal(timers.length, 1);
  w.state("ready");
  assert.equal(timers[0].live, false);
  assert.equal(fired(), 0);
});

test("a deliberate disconnect does not trigger it", () => {
  const { w, timers } = setup();
  w.state("ready");
  w.state("disconnected");  // the reconnector owns this case
  assert.equal(timers.length, 0);
});

test("a first connect is never cut short, however long the models take to load", () => {
  const { w, timers } = setup();
  w.state("initializing");
  w.state("connecting");
  w.state("connected");
  assert.equal(timers.length, 0);
});
