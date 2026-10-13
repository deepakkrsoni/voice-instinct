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
  r.trigger(); // only records a re-arm; no new timer until the attempt settles
  assert.equal(timers.length, 2); // retry timer + attempt timeout
  release();
  await tick();
  assert.equal(timers.length, 3); // the recorded trigger re-armed one retry
});

test("delay doubles per failure up to max, and succeeded() resets it", async () => {
  const { r, timers } = setup(async () => { throw new Error("down"); });
  r.trigger();
  const retries = () => timers.filter((t) => t.ms !== 15000);
  for (let i = 0; i < 4; i++) { retries()[i].fn(); await tick(); }
  assert.deepEqual(retries().map((t) => t.ms), [1000, 2000, 4000, 4000, 4000]);
  r.succeeded();
  retries()[4].fn(); await tick();
  assert.equal(retries()[5].ms, 1000);
});

test("a trigger during an in-flight connect re-arms one retry after it settles, even on success", async () => {
  let release;
  const { r, timers } = setup(() => new Promise((res) => (release = res)));
  r.trigger();
  timers[0].fn();
  await tick();
  r.trigger(); // Disconnected while connecting
  r.trigger();
  assert.equal(timers.length, 2); // retry timer + attempt timeout only
  release();
  await tick();
  assert.equal(timers.length, 3);
  assert.equal(timers[2].ms, 2000);
});

test("a hung connect times out and retries", async () => {
  const { r, timers } = setup(() => new Promise(() => {}));
  r.trigger();
  timers[0].fn();
  await tick();
  assert.equal(timers[1].ms, 15000);
  timers[1].fn(); // fire the attempt timeout
  await tick();
  assert.equal(timers.length, 3);
  r.trigger(); // still one retry pending: ignored
  assert.equal(timers.length, 3);
});

test("a retry while the client is still connecting does not call connect again", async () => {
  // client-js throws on a second connect() mid-connect, and the orb would flash its error look.
  let calls = 0;
  const timers = [];
  const r = makeReconnector(async () => { calls++; }, (fn, ms) => timers.push({ fn, ms }), { connecting: () => true });
  r.trigger();
  timers[0].fn();
  await tick();
  assert.equal(calls, 0);
  assert.equal(timers.length, 1); // settled at once: no attempt timeout, no further retry
});
