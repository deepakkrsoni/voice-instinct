const test = require("node:test");
const assert = require("node:assert/strict");
const { makeReconnector } = require("../renderer/reconnect.js");

function setup(connect) {
  const timers = [];
  const r = makeReconnector(connect, (fn, ms) => timers.push({ fn, ms }), { start: 1000, max: 4000 });
  return { r, timers };
}
const tick = () => new Promise((res) => setImmediate(res));

test("two triggers in a row schedule one retry", () => {
  const { r, timers } = setup(async () => {});
  r.trigger();
  r.trigger();
  assert.equal(timers.length, 1);
});

test("trigger is ignored while a connect is in flight", async () => {
  let release;
  const { r, timers } = setup(() => new Promise((res) => (release = res)));
  r.trigger();
  timers[0].fn();
  await tick();
  r.trigger();
  assert.equal(timers.length, 1);
  release();
  await tick();
  r.trigger();
  assert.equal(timers.length, 2);
});

test("delay doubles per failure up to max, and succeeded() resets it", async () => {
  const { r, timers } = setup(async () => { throw new Error("down"); });
  r.trigger();
  for (let i = 0; i < 4; i++) { timers[i].fn(); await tick(); }
  assert.deepEqual(timers.map((t) => t.ms), [1000, 2000, 4000, 4000, 4000]);
  r.succeeded();
  timers[4].fn(); await tick();
  assert.equal(timers[5].ms, 1000);
});
