// D181's shared-rate-awareness half of Pipeline Lab's debug-traffic.js
// (docs/lab/debug-traffic.js), extracted because p03-engine.js's resolveMaxAttempts()
// depends on it -- without this file, `typeof DebugTraffic === "undefined"` is always
// true and D181's elevated-retry-budget behavior silently never triggers.
//
// The original file is two things bolted together: this pure rate-checking half
// (fetchServerTimestamps/maybeFetchServerTimestamps/getCurrentRate -- no DOM reads at
// all) and a rolling-60s SVG traffic chart (render/wireTooltip/init, tied to
// #debug-traffic/#debug-traffic-svg element IDs that don't exist on these pages, since
// none of the 3 target screens include anything like that debug chart). Only the pure
// half is ported here, verbatim. Global name kept as `DebugTraffic` because
// p03-engine.js checks `typeof DebugTraffic` and calls `DebugTraffic.getCurrentRate()`
// by that name.
const DebugTraffic = (() => {
  const RATE_LIMIT_THRESHOLD = 40; // requests/60s -- nvidia-keypool-guard.py's documented free-tier figure
  const WINDOW_MS = 60 * 1000;
  // D167: throttles actual network/KV calls to once per this many ms, independent of
  // caller frequency -- reuses the last result (good or bad) in between instead of
  // calling again. Sized from the constraint itself: Workers KV's list() free-tier quota
  // is 1000/day: even a session left open unattended 24h at this interval makes
  // 86400/60 = 1440 calls (above 1000 in that edge case), but the realistic use here (a
  // single active debugging/interview session, minutes not all day) stays a wide margin
  // under it. Throttled whether the attempt succeeds OR fails, so a persistent
  // quota-exceeded state can't itself cause a hammering retry loop.
  const SERVER_FETCH_INTERVAL_MS = 60_000;

  let lastServerAttemptAt = 0;
  let lastServerTimestamps = null;

  async function fetchServerTimestamps() {
    const proxyUrl = LabConfig.get("proxy-url");
    if (!proxyUrl) return null;
    try {
      const base = proxyUrl.split("?")[0];
      const res = await fetch(`${base}?traffic=1`);
      if (!res.ok) return null;
      const data = await res.json();
      return Array.isArray(data.timestamps) ? data.timestamps : null;
    } catch (e) {
      return null; // proxy unreachable/misconfigured -- caller falls back to tab-only data
    }
  }

  async function maybeFetchServerTimestamps() {
    const now = Date.now();
    if (now - lastServerAttemptAt < SERVER_FETCH_INTERVAL_MS) return lastServerTimestamps;
    lastServerAttemptAt = now;
    lastServerTimestamps = await fetchServerTimestamps();
    return lastServerTimestamps;
  }

  // D181: exposes a rolling-60s count so a caller (p03-engine.js, before firing an
  // interview LLM call) can check current shared traffic and request more retry budget
  // if it's elevated. Returns isServerWide so callers can be honest about scope in their
  // own log messages (this-tab-only undercounts other teammates' traffic).
  async function getCurrentRate() {
    const serverTimestamps = await maybeFetchServerTimestamps();
    const isServerWide = serverTimestamps !== null;
    const log = isServerWide ? serverTimestamps : LabLLM.getRequestLog();
    const now = Date.now();
    const count = log.filter((ts) => ts > now - WINDOW_MS).length;
    return { count, isServerWide, threshold: RATE_LIMIT_THRESHOLD };
  }

  return { getCurrentRate };
})();
