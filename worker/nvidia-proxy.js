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
// Keep caller-controlled overrides bounded. This Worker is intentionally CORS-open, so
// an unbounded x-max-attempts value would otherwise let any caller exhaust Queue/KV quota.
const MAX_ATTEMPTS_CEILING = 10;
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

// NVIDIA Build currently allows about 40 starts/minute for the key used by this app.
// The configured production value is 36 RPM, leaving 10% headroom for clock/window
// boundaries and calls made outside this Worker. The limiter is global to this proxy:
// live LangSmith traces showed multiple NVIDIA key fingerprints can be active together,
// while 429s still arrive, so a per-key limiter does not protect the aggregate account.
const DEFAULT_NVIDIA_RPM_LIMIT = 36;
const MAX_NVIDIA_RPM_LIMIT = 40;
const LANGSMITH_DEFAULT_ENDPOINT = "https://api.smith.langchain.com";
const LANGSMITH_DEFAULT_PROJECT = "team-iz-nvidia-usage";

function positiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function nvidiaRpmLimit(env) {
  return Math.min(positiveInt(env.NVIDIA_RPM_LIMIT, DEFAULT_NVIDIA_RPM_LIMIT), MAX_NVIDIA_RPM_LIMIT);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function sha256Hex(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function nvidiaRateLimiterStub(env) {
  if (!env.NVIDIA_RATE_LIMITER) {
    throw new Error("NVIDIA_RATE_LIMITER binding is missing");
  }
  const id = env.NVIDIA_RATE_LIMITER.idFromName("nvidia-global");
  return env.NVIDIA_RATE_LIMITER.get(id);
}

async function reserveNvidiaRequestSlot(env, apiKey) {
  // The hash is for trace grouping only. Neither the raw key nor its hash determines the
  // limiter object; all traffic through this proxy shares one aggregate schedule.
  const keyHash = await sha256Hex(apiKey);
  const stub = nvidiaRateLimiterStub(env);
  const response = await stub.fetch("https://rate-limit.internal/acquire", { method: "POST" });
  if (!response.ok) throw new Error(`rate limiter returned HTTP ${response.status}`);
  const reservation = await response.json();
  return { ...reservation, keyFingerprint: keyHash.slice(0, 12) };
}

async function validateNvidiaRequestSlot(env, reservation) {
  const response = await nvidiaRateLimiterStub(env).fetch("https://rate-limit.internal/validate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ slot_at: reservation.slot_at, generation: reservation.generation }),
  });
  if (!response.ok) throw new Error(`rate limiter validation returned HTTP ${response.status}`);
  return response.json();
}

async function penalizeNvidiaRateLimit(env, cooldownMs) {
  const response = await nvidiaRateLimiterStub(env).fetch("https://rate-limit.internal/penalize", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ cooldown_ms: cooldownMs }),
  });
  if (!response.ok) throw new Error(`rate limiter cooldown returned HTTP ${response.status}`);
  return response.json();
}

async function waitForValidNvidiaRequestSlot(env, apiKey, onWait) {
  // A 429 can invalidate reservations that were already handed to sleeping consumers.
  // Revalidate just before every upstream fetch and reacquire if a newer cooldown exists.
  let totalWaitMs = 0;
  for (;;) {
    const reservation = await reserveNvidiaRequestSlot(env, apiKey);
    if (reservation.wait_ms > 0) {
      if (onWait) await onWait(reservation);
      await sleep(reservation.wait_ms);
      totalWaitMs += reservation.wait_ms;
    }
    const validation = await validateNvidiaRequestSlot(env, reservation);
    if (validation.valid) return { ...reservation, total_wait_ms: totalWaitMs };
  }
}

function parseNvidiaRequest(body) {
  try {
    const parsed = JSON.parse(body);
    return {
      model: typeof parsed.model === "string" ? parsed.model : "unknown",
      messageCount: Array.isArray(parsed.messages) ? parsed.messages.length : 0,
      maxTokens: Number.isFinite(parsed.max_tokens) ? parsed.max_tokens : null,
      stream: parsed.stream === true,
    };
  } catch (e) {
    return { model: "unknown", messageCount: 0, maxTokens: null, stream: false };
  }
}

function parseNvidiaUsage(responseText) {
  try {
    const parsed = JSON.parse(responseText);
    const usage = parsed && parsed.usage;
    if (!usage || typeof usage !== "object") return null;
    const inputTokens = Number(usage.prompt_tokens ?? usage.input_tokens);
    const outputTokens = Number(usage.completion_tokens ?? usage.output_tokens);
    const totalTokens = Number(usage.total_tokens);
    const metadata = {};
    if (Number.isFinite(inputTokens)) metadata.input_tokens = inputTokens;
    if (Number.isFinite(outputTokens)) metadata.output_tokens = outputTokens;
    if (Number.isFinite(totalTokens)) metadata.total_tokens = totalTokens;
    else if (Number.isFinite(inputTokens) && Number.isFinite(outputTokens)) {
      metadata.total_tokens = inputTokens + outputTokens;
    }
    return Object.keys(metadata).length ? metadata : null;
  } catch (e) {
    return null;
  }
}

async function sendLangSmithTrace(env, trace) {
  if (!env.LANGSMITH_API_KEY) return;
  const endpoint = String(env.LANGSMITH_ENDPOINT || LANGSMITH_DEFAULT_ENDPOINT).replace(/\/$/, "");
  const headers = {
    "content-type": "application/json",
    "x-api-key": env.LANGSMITH_API_KEY,
  };
  if (env.LANGSMITH_WORKSPACE_ID) headers["x-tenant-id"] = env.LANGSMITH_WORKSPACE_ID;
  if (env.LANGSMITH_ORGANIZATION_ID) headers["x-organization-id"] = env.LANGSMITH_ORGANIZATION_ID;

  const usageMetadata = trace.usage || undefined;
  const payload = {
    id: trace.id,
    name: "NVIDIA chat completion",
    run_type: "llm",
    start_time: new Date(trace.startedAt).toISOString(),
    end_time: new Date(trace.finishedAt).toISOString(),
    session_name: env.LANGSMITH_PROJECT || LANGSMITH_DEFAULT_PROJECT,
    tags: ["nvidia", "curriculum-manager", "production"],
    // Deliberately omit prompt and completion content. Usage/accounting does not need it.
    inputs: {
      model: trace.request.model,
      message_count: trace.request.messageCount,
      max_tokens: trace.request.maxTokens,
      stream: trace.request.stream,
    },
    outputs: {
      job_id: trace.jobId,
      attempt: trace.attempt,
      http_status: trace.httpStatus,
      status: trace.status,
      ...(usageMetadata ? { usage_metadata: usageMetadata } : {}),
    },
    extra: {
      metadata: {
        ls_provider: "nvidia",
        ls_model_name: trace.request.model,
        job_id: trace.jobId,
        attempt: trace.attempt,
        max_attempts: trace.maxAttempts,
        http_status: trace.httpStatus,
        rate_limited: trace.httpStatus === 429,
        limiter_wait_ms: trace.limiterWaitMs,
        limiter_scope: "proxy-global",
        nvidia_key_fingerprint: trace.keyFingerprint,
        ...(usageMetadata ? { usage_metadata: usageMetadata } : {}),
      },
    },
    ...(trace.error ? { error: trace.error.slice(0, 500) } : {}),
  };

  const response = await fetch(`${endpoint}/runs`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`LangSmith HTTP ${response.status}: ${detail.slice(0, 300)}`);
  }
}

function scheduleLangSmithTrace(ctx, env, trace) {
  const promise = sendLangSmithTrace(env, trace).catch(() => {
    // Telemetry must never fail or delay an NVIDIA job. LangSmith availability is not
    // part of the model-serving correctness boundary.
  });
  if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(promise);
  return promise;
}

// One global object advances the proxy-wide schedule, preventing separate curriculum
// runs or different NVIDIA keys in the same account from independently bursting at 40.
export class NvidiaRateLimiter {
  constructor(state, env) {
    this.state = state;
    this.env = env;
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "POST") {
      return new Response("not found", { status: 404 });
    }
    const rpm = nvidiaRpmLimit(this.env);
    const intervalMs = Math.ceil(60_000 / rpm);
    const now = Date.now();

    if (url.pathname === "/acquire") {
      let slotAt = now;
      let generation = 0;
      await this.state.storage.transaction(async (txn) => {
        const nextSlotAt = Number(await txn.get("next_slot_at")) || now;
        const blockedUntil = Number(await txn.get("blocked_until")) || 0;
        generation = Number(await txn.get("generation")) || 0;
        slotAt = Math.max(now, nextSlotAt, blockedUntil);
        await txn.put("next_slot_at", slotAt + intervalMs);
      });
      return Response.json({
        wait_ms: Math.max(0, slotAt - now),
        slot_at: slotAt,
        generation,
        rpm,
      });
    }

    if (url.pathname === "/validate") {
      const body = await request.json().catch(() => ({}));
      const generation = Number(await this.state.storage.get("generation")) || 0;
      const blockedUntil = Number(await this.state.storage.get("blocked_until")) || 0;
      const slotAt = Number(body.slot_at) || 0;
      const valid = Number(body.generation) === generation && now >= blockedUntil && now + 25 >= slotAt;
      return Response.json({ valid, generation, blocked_until: blockedUntil });
    }

    if (url.pathname === "/penalize") {
      const body = await request.json().catch(() => ({}));
      const cooldownMs = Math.min(Math.max(positiveInt(body.cooldown_ms, 60_000), 1_000), 300_000);
      let blockedUntil = now + cooldownMs;
      let generation = 0;
      await this.state.storage.transaction(async (txn) => {
        const currentBlock = Number(await txn.get("blocked_until")) || 0;
        const currentGeneration = Number(await txn.get("generation")) || 0;
        blockedUntil = Math.max(currentBlock, now + cooldownMs);
        generation = currentGeneration + 1;
        await txn.put("blocked_until", blockedUntil);
        await txn.put("next_slot_at", blockedUntil);
        await txn.put("generation", generation);
      });
      return Response.json({ blocked_until: blockedUntil, generation, rpm });
    }

    return new Response("not found", { status: 404 });
  }
}

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

    // GET /?models=1 -- D-catalog (2026-07-24): same feature as the code-qna Worker's own
    // ?models=1 (see docs/lab/code-qna/worker/nvidia-proxy.js for the full WHY/COST/EXIT --
    // not repeated here). Dumb passthrough+cache only; filtering/curation lives client-side
    // in lab-core.js so it ships via plain `git push`, not a Worker redeploy.
    if (request.method === "GET" && url.searchParams.has("models")) {
      const apiKey = request.headers.get("x-nvidia-api-key");
      if (!apiKey) return jsonResponse({ error: "missing x-nvidia-api-key header" }, 401, origin);
      const CACHE_KEY = "models_catalog_cache";
      const CACHE_TTL_SECONDS = 6 * 3600;
      try {
        const cached = await env.NVIDIA_JOBS.get(CACHE_KEY);
        if (cached) return jsonResponse(JSON.parse(cached), 200, origin);
        const upstream = await fetch("https://integrate.api.nvidia.com/v1/models", {
          headers: { authorization: `Bearer ${apiKey}` },
        });
        if (!upstream.ok) {
          return jsonResponse({ error: `NVIDIA models list failed: HTTP ${upstream.status}` }, upstream.status, origin);
        }
        const data = await upstream.json();
        await env.NVIDIA_JOBS.put(CACHE_KEY, JSON.stringify(data), { expirationTtl: CACHE_TTL_SECONDS });
        return jsonResponse(data, 200, origin);
      } catch (e) {
        return jsonResponse({ error: `models list error: ${e.message}` }, 500, origin);
      }
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
    // Absent/invalid falls back to MAX_ATTEMPTS below unchanged, so P01's own
    // refine/question-gen calls (which never send this header) keep today's behavior
    // exactly as-is -- this is opt-in per request, not a global policy change.
    const maxAttemptsHeader = request.headers.get("x-max-attempts");
    const maxAttemptsOverride = maxAttemptsHeader ? parseInt(maxAttemptsHeader, 10) : NaN;
    const maxAttempts = Number.isInteger(maxAttemptsOverride) && maxAttemptsOverride > 0
      ? Math.min(maxAttemptsOverride, MAX_ATTEMPTS_CEILING)
      : undefined;

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
  async queue(batch, env, ctx) {
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

      // Reserve an exact proxy-global slot before starting the upstream request. Unlike
      // the old per-run CHUNK_CONCURRENCY cap, this schedule is shared across all jobs.
      let reservation;
      try {
        reservation = await waitForValidNvidiaRequestSlot(env, apiKey, async (pendingReservation) => {
          await putIfNotTerminal(env, jobId, {
            status: "pending",
            note: `global ${pendingReservation.rpm} RPM limiter: waiting ${Math.ceil(pendingReservation.wait_ms / 1000)}s`,
          });
        });
      } catch (e) {
        const wrote = await putIfNotTerminal(env, jobId, {
          status: "pending",
          note: `global rate limiter unavailable: ${e.message}; retrying`,
        });
        if (wrote) message.retry({ delaySeconds: RETRY_DELAY_SECONDS });
        else message.ack();
        continue;
      }

      // A duplicate delivery may have completed this job while this invocation waited
      // for its reserved slot. Do not make an unnecessary NVIDIA call in that case.
      if (await isAlreadyTerminal(env, jobId)) {
        message.ack();
        continue;
      }

      const requestMeta = parseNvidiaRequest(body);
      const startedAt = Date.now();
      const traceBase = {
        id: crypto.randomUUID(),
        jobId,
        attempt: message.attempts,
        maxAttempts: effectiveMaxAttempts,
        request: requestMeta,
        startedAt,
        limiterWaitMs: reservation.total_wait_ms || 0,
        keyFingerprint: reservation.keyFingerprint,
      };
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
          scheduleLangSmithTrace(ctx, env, {
            ...traceBase,
            finishedAt: Date.now(),
            httpStatus: upstream.status,
            status: "success",
            usage: parseNvidiaUsage(text),
          });
          await putIfNotTerminal(env, jobId, { status: "done", result: text });
          message.ack();
          continue;
        }

        const upstreamError = `NVIDIA HTTP ${upstream.status}: ${text.slice(0, 500)}`;
        if (upstream.status === 429) {
          // Invalidate every reservation already issued to other sleeping consumers and
          // force the whole proxy to wait out NVIDIA's rolling limit window.
          try {
            await penalizeNvidiaRateLimit(env, RATE_LIMIT_RETRY_DELAY_SECONDS * 1000);
          } catch (e) {
            // The 429 is still returned and retried normally if telemetry/control-plane
            // state is briefly unavailable; do not turn it into a different job error.
          }
        }
        scheduleLangSmithTrace(ctx, env, {
          ...traceBase,
          finishedAt: Date.now(),
          httpStatus: upstream.status,
          status: "error",
          error: upstreamError,
        });

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
          // 429 needs a full-window client retry delay. Keep this explicit instead of
          // forcing the orchestrator to parse a human-readable error string.
          rateLimited: upstream.status === 429,
        });
        message.ack();
      } catch (e) {
        clearTimeout(timeoutId);
        const isTimeout = e.name === "AbortError";
        const reason = isTimeout ? `no response within ${PER_ATTEMPT_TIMEOUT_MS / 1000}s` : `upstream fetch failed: ${e.message}`;
        scheduleLangSmithTrace(ctx, env, {
          ...traceBase,
          finishedAt: Date.now(),
          httpStatus: 0,
          status: isTimeout ? "timeout" : "error",
          error: reason,
        });

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
