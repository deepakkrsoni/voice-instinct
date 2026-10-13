// Retry with backoff, at most one pending retry or in-flight connect at a time. Unit-tested.
function makeReconnector(connect, schedule = setTimeout, { start = 1000, max = 30000 } = {}) {
  let delay = start;
  let busy = false; // a retry is pending or a connect is in flight
  const run = () => Promise.resolve().then(connect).then(
    () => { busy = false; },
    () => { busy = false; trigger(); },
  );
  function trigger() {
    if (busy) return;
    busy = true;
    const ms = delay;
    delay = Math.min(delay * 2, max);
    schedule(run, ms);
  }
  return { trigger, succeeded: () => { delay = start; } };
}

module.exports = { makeReconnector };
