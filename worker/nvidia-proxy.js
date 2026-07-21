/**
 * D-C (PLAN.md): integrate.api.nvidia.com has no Access-Control-Allow-Origin header
 * (verified 2026-07-14), so browser calls from docs/lab/ must go through a proxy.
 * This is that proxy -- deployable by anyone on the team as their own Cloudflare
 * Worker, then pasted into the tool's "LLM 프록시 URL" field. The whole point of
 * publishing this file in the repo is that anyone can read exactly what it does with
 * the API key they paste into it.
 *
 * Deploy: `cd worker && wrangler deploy` (needs `wrangler.toml`'s kv_namespaces/queues
 * bindings, which need a KV namespace + Queue created once per account -- see
 * experiments/web_lab/SETUP.md). Needs a free Cloudflare account + `wrangler login`.
 *
 * D-H (2026-07-14, README D143): async job-queue redesign, replacing D-E's streaming
 * attempt. Cloudflare's free-tier edge gives up after ~100-125s of silence on a single
 * request -- and NVIDIA build-tier models normally take up to several minutes under load
 * (feedback/nvidia_client.py's DEFAULT_TIMEOUT_S=600, with D98's own history of "up to
 * ~300s+ under load" -- confirmed live this session: qwen3-next-80b took 92s on one call
 * and didn't respond at all within 150s on another). Streaming (D-E) only helps once
 * NVIDIA starts sending bytes; it can't help if NVIDIA is slow to send the FIRST byte,
 * which is exactly what kept happening. No client-facing HTTP request can survive that
 * wait, streamed or not -- so this doesn't try to keep one alive. POST submits a job and
 * returns a job_id almost instantly; the actual NVIDIA call happens in queue() below,
 * a Cloudflare Queue consumer invocation, which gets up to 15 minutes wall-clock with no
 * penalty for I/O wait (Cloudflare's documented queue consumer limit -- verified against
 * https://developers.cloudflare.com/queues/platform/limits/). GET polls for the result.
 *
 * D-D (PLAN.md) still mostly holds but has one real change worth being explicit about:
 * the API key is no longer used-and-discarded within a single request/response. It now
 * travels inside the queued job message (Cloudflare Queues retains messages up to 24h)
 * so the consumer invocation can use it once NVIDIA is ready to be called. It is still
 * never written to KV (only job status/result is), never logged, never returned in any
 * response. The trust boundary is "whoever controls this Cloudflare account" either way --
 * the Worker code already had the raw key in memory before this change too -- but it's no
 * longer strictly "in memory for the duration of one HTTP request and then gone", so this
 * is recorded here rather than left as an unstated regression from D-D's original claim.
 *
 * D-I (2026-07-14): retry-with-backoff in queue(), replacing D-H's "always ack, never
 * retry" stance. D-H assumed a failure meant NVIDIA had genuinely rejected the request,
 * so retrying would "just repeat the same slow/failed call." That assumption was tested
 * and falsified this session: a direct curl to the same endpoint, bypassing every piece
 * of this project's own infrastructure (no Worker, no Queue), got a clean 200 after
 * 236.7s on a heavy prompt -- well past the ~100-125s mark that both D-H and a since-
 * observed queue-consumer 524 looked like a hard ceiling. The failures aren't a
 * deterministic timeout; they're intermittent NVIDIA-side flakiness (same conclusion as
 * README's D142, now with more data) -- the same kind of request can fail once and
 * succeed on a later attempt. So each attempt now gets its own AbortController timeout
 * (600s, matching feedback/nvidia_client.py's DEFAULT_TIMEOUT_S so this isn't a new
 * made-up number), and a retryable failure (timeout, network error, or NVIDIA returning
 * 429/500/502/503/524) calls message.retry() instead of message.ack(). This relies on
 * Cloudflare's own queue redelivery (verified against
 * https://developers.cloudflare.com/queues/configuration/javascript-apis/): retry()
 * redelivers the message as a brand-new consumer invocation, which gets its own fresh
 * 15-minute wall-clock budget rather than looping inside this one, so retrying doesn't
 * eat into the same invocation's time. message.attempts (also part of that API,
 * 1-indexed) caps this at MAX_ATTEMPTS total before giving up and writing a terminal
 * "error" record; a non-retryable status (e.g. 400/401 -- a real client error, not
 * flakiness) still fails immediately, since retrying a malformed request or bad key
 * would just waste attempts.
 *   WHY: observed failures aren't deterministic -- a second attempt has a real chance
 *     of succeeding, confirmed live (see above).
 *   COST: a job that keeps hitting retryable failures can now take up to MAX_ATTEMPTS *
 *     ~600s (worst case ~30 min) before the client sees a terminal error, instead of
 *     failing after one bad response in a few seconds. JOB_TTL_SECONDS (1h) already
 *     covers this window.
 *   EXIT: if NVIDIA's flakiness turns out to correlate with something identifiable (time
 *     of day, a specific NVIDIA_API_KEY_N, request size), replace the blind retry with a
 *     targeted fix once that pattern is confirmed with data -- don't keep raising
 *     MAX_ATTEMPTS as a substitute for finding the actual pattern.
 */

const NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions";

// Restrict to your GitHub Pages origin once you know it -- "*" works but means any
// website could relay calls through your worker using a visitor's own pasted key
// (that key is still theirs, but it's needless exposure of your worker as an open relay).
const ALLOWED_ORIGIN = "*"; // e.g. "https://popixoxipop-collab.github.io"

const JOB_TTL_SECONDS = 3600; // 1 hour -- generous for an actively-polling client, not indefinite

// D-I: retryable = NVIDIA/edge-side flakiness worth a second attempt; anything else
// (4xx other than 429) is a real client-side error a retry can't fix.
const RETRYABLE_STATUSES = new Set([429, 500, 502, 503, 524]);
const MAX_ATTEMPTS = 3; // message.attempts is 1-indexed; this allows 2 retries
const RETRY_DELAY_SECONDS = 5; // brief gap before Cloudflare redelivers -- avoid hammering NVIDIA back-to-back
// D159 (2026-07-15): 429 specifically means "you're being rate-limited, wait longer" --
// a real 26-way parallel chunk-analysis burst (D156) hit this on a later, unrelated call
// (question-generation) using the same key, consistent with nvidia-keypool-guard.py's
// own documented ~40rpm free-tier ceiling. A 5s retry can't outlast a per-minute window;
// 429 specifically now waits the length of that window instead. Every OTHER retryable
// status (500/502/503/524, timeouts) keeps the short 5s delay -- those are generic
// transient blips, not a "you're over budget, wait out the window" signal, so forcing
// them to wait a full minute too would just slow down otherwise-quick recoveries.
const RATE_LIMIT_RETRY_DELAY_SECONDS = 60;
const PER_ATTEMPT_TIMEOUT_MS = 600_000; // 600s, == feedback/nvidia_client.py's DEFAULT_TIMEOUT_S (D98-derived, not a new guess)

// D160 (2026-07-15): docs/lab/debug-traffic.js's graph only counted requests one browser
// tab initiated -- it couldn't see server-side retries inside queue() below, so a burst
// that looked under the 40rpm line there could still be over budget once retries (or
// other teammates going through this same deployed Worker) are counted. Records the
// timestamp of every ACTUAL fetch() to NVIDIA here (first attempt and every retry alike,
// from every job/every client), one unique KV key per sample.
//   WHY unique keys, not one shared "list of timestamps" key: D-J already established
//     that this KV can have many concurrent consumer invocations (parallel chunk jobs,
//     retries, other teammates) -- a shared key would need read-modify-write-append,
//     which races exactly like D-J's duplicate-delivery problem (two invocations read
//     the same list, both append, the loser's write is silently lost). A unique key per
//     sample turns every write into an unconditional put -- no read, no race, ever.
//   COST: relies on KV list() to reconstruct the log for reading (see the new GET
//     ?traffic=1 handler below) -- fine at this scale (a handful to low hundreds of
//     samples in the 5-minute window), would need Analytics Engine or a Durable Object
//     if traffic ever got large enough for list() itself to become the bottleneck.
//   EXIT: if this KV traffic namespace ever needs its own lifecycle separate from job
//     records, split it into a second KV binding -- not necessary at this scale.
const TRAFFIC_SAMPLE_TTL_SECONDS = 300; // matches docs/lab/debug-traffic.js's 5-minute HISTORY_MS

async function recordTrafficSample(env) {
  try {
    const key = `traffic:${Date.now()}:${crypto.randomUUID()}`;
    await env.NVIDIA_JOBS.put(key, "1", { expirationTtl: TRAFFIC_SAMPLE_TTL_SECONDS });
  } catch (e) {
    // best-effort -- a dropped traffic sample must never fail or delay the real NVIDIA call
  }
}

function corsHeaders(origin) {
  return {
    "access-control-allow-origin": ALLOWED_ORIGIN === "*" ? "*" : ALLOWED_ORIGIN,
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type, x-nvidia-api-key, x-max-attempts",
    "access-control-max-age": "86400",
  };
}

function jsonResponse(obj, status, origin) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", ...corsHeaders(origin) },
  });
}

// D-J (2026-07-15, README D145): Cloudflare Queues is at-least-once delivery -- the same
// message can reach queue() more than once (observed live: a job's KV record flapped
// error -> pending -> error several times a few seconds apart after the job had already
// reached attempt 3/3 and failed terminally, before settling). A stale/duplicate delivery
// that's still mid-retry can land its "pending, retrying" write AFTER a different
// delivery already wrote the real terminal done/error record, un-terminating a job the
// client may already be about to see resolved -- or worse, a stale delivery's terminal
// write could clobber a genuine "done" with a stale "error" (or vice versa) if timing
// goes the other way. Every write in queue() now goes through this: once a job is
// done/error, nothing else is allowed to overwrite it.
//   WHY: at-least-once delivery is Cloudflare's documented guarantee, not a bug on their
//     end -- consumers are expected to be idempotent against duplicate/stale deliveries.
//   COST: one extra KV read before every write in queue() (KV reads are cheap/fast
//     compared to the NVIDIA call this whole function exists to make).
//   EXIT: doesn't fully close the race (get-then-put isn't atomic -- KV has no
//     conditional/compare-and-swap put) -- a true fix would need a Durable Object per
//     job instead of KV. Not worth it for a status-polling endpoint; revisit only if a
//     "done" is ever observed to get clobbered by a stale "error" (the costlier
//     direction of this race) in practice.
async function isAlreadyTerminal(env, jobId) {
  const existingRaw = await env.NVIDIA_JOBS.get(jobId);
  if (!existingRaw) return false;
  try {
    const existing = JSON.parse(existingRaw);
    return existing.status === "done" || existing.status === "error";
  } catch (e) {
    return false; // malformed existing record -- treat as not-terminal, let it get overwritten
  }
}

// Returns true if it actually wrote (false = skipped, another delivery already finished this job).
async function putIfNotTerminal(env, jobId, record) {
  if (await isAlreadyTerminal(env, jobId)) return false;
  await env.NVIDIA_JOBS.put(jobId, JSON.stringify(record), { expirationTtl: JOB_TTL_SECONDS });
  return true;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("origin") || "";
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    // GET /?traffic=1 -- D160: recent actual NVIDIA request timestamps (every attempt,
    // first + retries, from every client through this Worker) for docs/lab/debug-traffic.js.
    // Read-only, best-effort -- never blocks or affects job submission/polling.
    if (request.method === "GET" && url.searchParams.has("traffic")) {
      const list = await env.NVIDIA_JOBS.list({ prefix: "traffic:" });
      const timestamps = list.keys
        .map((k) => Number(k.name.split(":")[1]))
        .filter((n) => Number.isFinite(n));
      return jsonResponse({ timestamps }, 200, origin);
    }

    // GET /?job=<id> -- poll for a previously-submitted job.
    if (request.method === "GET") {
      const jobId = url.searchParams.get("job");
      if (!jobId) return jsonResponse({ error: "missing job query param" }, 400, origin);
      const raw = await env.NVIDIA_JOBS.get(jobId);
      if (!raw) return jsonResponse({ error: "unknown or expired job" }, 404, origin);
      return jsonResponse(JSON.parse(raw), 200, origin);
    }

    if (request.method !== "POST") {
      return jsonResponse({ error: "GET (poll) or POST (submit) only" }, 405, origin);
    }

    // POST -- submit a new job. Returns immediately; the real NVIDIA call happens in
    // queue() below, decoupled from this request/response entirely.
    const apiKey = request.headers.get("x-nvidia-api-key");
    if (!apiKey) return jsonResponse({ error: "missing x-nvidia-api-key header" }, 401, origin);

    let body;
    try {
      body = await request.text();
      JSON.parse(body); // fail fast on malformed input rather than queueing garbage
    } catch (e) {
      return jsonResponse({ error: "request body must be valid JSON" }, 400, origin);
    }

    // D169 (2026-07-15): optional per-job attempt-count override, requested by the client
    // via this header -- lets P01's chunk-analysis stage own its own coordinated retry
    // (see docs/lab/p01-runner.js) instead of each job independently retrying per D-I.
    // Absent/invalid falls back to MAX_ATTEMPTS below unchanged, so P03 (and P01's own
    // refine/question-gen calls, which never send this header) keep today's behavior
    // exactly as-is -- this is opt-in per request, not a global policy change.
    const maxAttemptsHeader = request.headers.get("x-max-attempts");
    const maxAttemptsOverride = maxAttemptsHeader ? parseInt(maxAttemptsHeader, 10) : NaN;
    const maxAttempts = Number.isInteger(maxAttemptsOverride) && maxAttemptsOverride > 0 ? maxAttemptsOverride : undefined;

    const jobId = crypto.randomUUID();
    await env.NVIDIA_JOBS.put(jobId, JSON.stringify({ status: "pending" }), { expirationTtl: JOB_TTL_SECONDS });

    try {
      await env.NVIDIA_JOBS_QUEUE.send({ jobId, apiKey, body, maxAttempts });
    } catch (e) {
      await env.NVIDIA_JOBS.put(
        jobId,
        JSON.stringify({ status: "error", error: `queue send failed: ${e.message}` }),
        { expirationTtl: JOB_TTL_SECONDS }
      );
      return jsonResponse({ error: `queue send failed: ${e.message}` }, 502, origin);
    }

    return jsonResponse({ job_id: jobId }, 202, origin);
  },

  // Consumer: one job per invocation (max_batch_size=1 in wrangler.toml, so one slow
  // NVIDIA call never blocks a teammate's job queued behind it). No client is waiting on
  // this directly, so there's no 100s-class timeout to survive -- but see D-I above for
  // why a single attempt still isn't the end of the story.
  async queue(batch, env) {
    for (const message of batch.messages) {
      const { jobId, apiKey, body, maxAttempts } = message.body;
      // D169: per-job override (see the POST handler above) falls back to the existing
      // MAX_ATTEMPTS when absent -- every caller that predates this change keeps behaving
      // exactly as before.
      const effectiveMaxAttempts = maxAttempts || MAX_ATTEMPTS;

      // D-J: cheap upfront exit for the common case -- a duplicate/stale delivery of a
      // job some other delivery already finished. Skips the NVIDIA call entirely instead
      // of just discarding the result at write-time.
      if (await isAlreadyTerminal(env, jobId)) {
        message.ack();
        continue;
      }

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), PER_ATTEMPT_TIMEOUT_MS);
      try {
        await recordTrafficSample(env); // D160: log this attempt (first or retry) before the real call
        const upstream = await fetch(NVIDIA_URL, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}` },
          body,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);
        const text = await upstream.text();

        if (upstream.ok) {
          await putIfNotTerminal(env, jobId, { status: "done", result: text });
          message.ack();
          continue;
        }

        if (RETRYABLE_STATUSES.has(upstream.status) && message.attempts < effectiveMaxAttempts) {
          const delaySeconds = upstream.status === 429 ? RATE_LIMIT_RETRY_DELAY_SECONDS : RETRY_DELAY_SECONDS;
          const wrote = await putIfNotTerminal(env, jobId, {
            status: "pending",
            note: `attempt ${message.attempts}/${effectiveMaxAttempts} got HTTP ${upstream.status}, retrying in ${delaySeconds}s`,
          });
          // If another delivery already reached done/error, this job's fate is already
          // sealed -- don't retry a job nobody's waiting on anymore, just ack it away.
          if (wrote) message.retry({ delaySeconds });
          else message.ack();
          continue;
        }

        await putIfNotTerminal(env, jobId, {
          status: "error",
          error: `NVIDIA HTTP ${upstream.status} (attempt ${message.attempts}/${effectiveMaxAttempts}): ${text.slice(0, 500)}`,
          // D169: lets a client that owns its own retry (x-max-attempts) know whether
          // resubmitting this job later is worth it, without string-matching the error.
          retryable: RETRYABLE_STATUSES.has(upstream.status),
        });
        message.ack();
      } catch (e) {
        clearTimeout(timeoutId);
        const isTimeout = e.name === "AbortError";
        const reason = isTimeout ? `no response within ${PER_ATTEMPT_TIMEOUT_MS / 1000}s` : `upstream fetch failed: ${e.message}`;

        if (message.attempts < effectiveMaxAttempts) {
          const wrote = await putIfNotTerminal(env, jobId, {
            status: "pending",
            note: `attempt ${message.attempts}/${effectiveMaxAttempts} ${reason}, retrying`,
          });
          if (wrote) message.retry({ delaySeconds: RETRY_DELAY_SECONDS });
          else message.ack();
          continue;
        }

        await putIfNotTerminal(env, jobId, {
          status: "error",
          error: `${reason} (attempt ${message.attempts}/${effectiveMaxAttempts}, giving up)`,
          retryable: true, // timeouts/network errors are always worth a client-driven retry later
        });
        message.ack();
      }
    }
  },
};
