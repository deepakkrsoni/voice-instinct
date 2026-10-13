// Retry with backoff: at most one pending retry or in-flight connect at a time, each attempt
// bounded by a timeout. A trigger during an attempt re-arms one retry after it settles. Unit-tested.
// connecting(): the client is still connecting or already connected, so another connect() would only
// throw (and flash the orb's error look). ponytail: a connect hung in "connecting" is then never retried.
function makeReconnector(connect, schedule = setTimeout, { start = 1000, max = 30000, timeout = 15000, connecting = () => false } = {}) {
  let delay = start;
  let busy = false; // a retry is pending or a connect is in flight
  let again = false; // a trigger arrived while busy
  const run = () => {
    if (connecting()) { busy = false; again = false; return; }
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

// Stall watchdog: the transport can leave "ready" (media stopped, peer dropped) without ever reporting
// Disconnected. If it is not back to "ready" within `ms`, `restart` runs. "disconnected" is excluded:
// the reconnector already handles a real disconnect. Unit-tested.
function makeWatchdog(restart, schedule = setTimeout, cancel = clearTimeout, ms = 15000) {
  let timer = null;
  let live = false; // reached "ready" since the last disconnect: a first connect is never cut short
  return {
    state(s) {
      if (s === "ready" || s === "disconnected") {
        live = s === "ready";
        if (timer) { cancel(timer); timer = null; }
      } else if (live && !timer) {
        timer = schedule(() => { timer = null; restart(); }, ms);
      }
    },
  };
}

module.exports = { makeReconnector, makeWatchdog };
