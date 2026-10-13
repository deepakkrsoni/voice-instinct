// Retry with backoff: at most one pending retry or in-flight connect at a time, each attempt
// bounded by a timeout. A trigger during an attempt re-arms one retry after it settles. Unit-tested.
function makeReconnector(connect, schedule = setTimeout, { start = 1000, max = 30000, timeout = 15000 } = {}) {
  let delay = start;
  let busy = false; // a retry is pending or a connect is in flight
  let again = false; // a trigger arrived while busy
  const run = () => {
    let settled = false;
    const settle = (failed) => {
      if (settled) return; // late result of a timed-out attempt
      settled = true;
      busy = false;
      if (failed || again) { again = false; trigger(); }
    };
    schedule(() => settle(true), timeout);
    Promise.resolve().then(connect).then(() => settle(false), () => settle(true));
  };
  function trigger() {
    if (busy) { again = true; return; }
    busy = true;
    const ms = delay;
    delay = Math.min(delay * 2, max);
    schedule(run, ms);
  }
  return { trigger, succeeded: () => { delay = start; } };
}

module.exports = { makeReconnector };
