import assert from "node:assert/strict";
import test from "node:test";

import worker, { NvidiaRateLimiter } from "./nvidia-proxy.js";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  async get(key) {
    return this.values.get(key);
  }

  async put(key, value) {
    this.values.set(key, value);
  }

  async transaction(callback) {
    return callback(this);
  }
}

class MemoryKv {
  constructor() {
    this.values = new Map();
  }

  async get(key) {
    return this.values.get(key) ?? null;
  }

  async put(key, value) {
    this.values.set(key, value);
  }
}

function limiterBinding(response = { wait_ms: 0, slot_at: Date.now(), generation: 0, rpm: 36 }) {
  return {
    idFromName(name) {
      assert.equal(name, "nvidia-global");
      assert.equal(name.includes("nvapi-"), false);
      return name;
    },
    get() {
      return {
        async fetch(url) {
          if (String(url).endsWith("/validate")) return Response.json({ valid: true, generation: 0 });
          if (String(url).endsWith("/penalize")) return Response.json({ generation: 1 });
          return Response.json(response);
        },
      };
    },
  };
}

function queueMessage(body) {
  return {
    body,
    attempts: 1,
    acked: false,
    retried: false,
    ack() {
      this.acked = true;
    },
    retry() {
      this.retried = true;
    },
  };
}

test("rate limiter spaces all reservations and caps configuration at 40 RPM", async () => {
  const realNow = Date.now;
  Date.now = () => 1_000_000;
  try {
    const limiter = new NvidiaRateLimiter(
      { storage: new MemoryStorage() },
      { NVIDIA_RPM_LIMIT: "999" }
    );
    const first = await (await limiter.fetch(new Request("https://rate-limit.internal/acquire", { method: "POST" }))).json();
    const second = await (await limiter.fetch(new Request("https://rate-limit.internal/acquire", { method: "POST" }))).json();
    const third = await (await limiter.fetch(new Request("https://rate-limit.internal/acquire", { method: "POST" }))).json();
    assert.deepEqual([first.wait_ms, second.wait_ms, third.wait_ms], [0, 1500, 3000]);
    assert.equal(first.rpm, 40);

    const penalty = await (await limiter.fetch(new Request("https://rate-limit.internal/penalize", {
      method: "POST",
      body: JSON.stringify({ cooldown_ms: 60_000 }),
    }))).json();
    assert.equal(penalty.generation, 1);
    assert.equal(penalty.blocked_until, 1_060_000);
    const oldReservation = await (await limiter.fetch(new Request("https://rate-limit.internal/validate", {
      method: "POST",
      body: JSON.stringify({ slot_at: first.slot_at, generation: first.generation }),
    }))).json();
    assert.equal(oldReservation.valid, false);
    const afterPenalty = await (await limiter.fetch(new Request("https://rate-limit.internal/acquire", { method: "POST" }))).json();
    assert.equal(afterPenalty.wait_ms, 60_000);
    assert.equal(afterPenalty.generation, 1);
  } finally {
    Date.now = realNow;
  }
});

test("successful NVIDIA attempt records sanitized LangSmith usage metadata", async () => {
  const kv = new MemoryKv();
  await kv.put("job-1", JSON.stringify({ status: "pending" }));
  const message = queueMessage({
    jobId: "job-1",
    apiKey: "nvapi-super-secret",
    body: JSON.stringify({
      model: "minimaxai/minimax-m3",
      messages: [{ role: "user", content: "private prompt must not be traced" }],
      max_tokens: 512,
    }),
    maxAttempts: 1,
  });
  const waits = [];
  const requests = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).includes("integrate.api.nvidia.com")) {
      return Response.json({
        id: "completion-1",
        choices: [{ message: { role: "assistant", content: "private response" } }],
        usage: { prompt_tokens: 120, completion_tokens: 30, total_tokens: 150 },
      });
    }
    if (String(url).endsWith("/runs")) return new Response(null, { status: 202 });
    throw new Error(`unexpected fetch: ${url}`);
  };

  try {
    await worker.queue(
      { messages: [message] },
      {
        NVIDIA_JOBS: kv,
        NVIDIA_RATE_LIMITER: limiterBinding(),
        LANGSMITH_API_KEY: "langsmith-secret",
        LANGSMITH_PROJECT: "team-iz-nvidia-usage",
        MODEL_INPUT_USD_PER_MILLION: "0.24",
        MODEL_OUTPUT_USD_PER_MILLION: "0.96",
      },
      { waitUntil(promise) { waits.push(promise); } }
    );
    await Promise.all(waits);
  } finally {
    globalThis.fetch = realFetch;
  }

  assert.equal(message.acked, true);
  assert.equal(JSON.parse(await kv.get("job-1")).status, "done");
  const langSmithRequest = requests.find((entry) => entry.url.endsWith("/runs"));
  assert.ok(langSmithRequest);
  const trace = JSON.parse(langSmithRequest.options.body);
  assert.equal(trace.session_name, "team-iz-nvidia-usage");
  assert.equal(trace.trace_id, trace.id);
  assert.match(trace.dotted_order, /^\d{8}T\d{12}Z.+$/);
  assert.deepEqual(trace.tags, ["nvidia", "curriculum-manager", "production"]);
  // D-ls-t3: the field the backend actually reads (extra.metadata.usage_metadata), not
  // just its SDK-parity mirror under outputs.
  assert.equal(trace.extra.metadata.usage_metadata.input_tokens, 120);
  assert.equal(trace.extra.metadata.usage_metadata.output_tokens, 30);
  assert.equal(trace.extra.metadata.usage_metadata.total_tokens, 150);
  assert.equal(trace.outputs.usage_metadata.input_tokens, 120);
  assert.equal(trace.outputs.usage_metadata.output_tokens, 30);
  assert.equal(trace.outputs.usage_metadata.total_tokens, 150);
  assert.equal(trace.outputs.usage_metadata.input_cost, 0.0000288);
  assert.equal(trace.outputs.usage_metadata.output_cost, 0.0000288);
  assert.equal(trace.outputs.usage_metadata.total_cost, 0.0000576);
  assert.equal(trace.extra.metadata.ls_model_name, "minimaxai/minimax-m3");
  assert.equal(langSmithRequest.options.body.includes("private prompt"), false);
  assert.equal(langSmithRequest.options.body.includes("private response"), false);
  assert.equal(langSmithRequest.options.body.includes("nvapi-super-secret"), false);
});

test("LangSmith usage metadata surfaces reasoning/cached token subtypes", async () => {
  const kv = new MemoryKv();
  await kv.put("job-2", JSON.stringify({ status: "pending" }));
  const message = queueMessage({
    jobId: "job-2",
    apiKey: "nvapi-test-2",
    body: JSON.stringify({ model: "minimaxai/minimax-m3", messages: [{ role: "user", content: "hi" }] }),
    maxAttempts: 1,
  });
  const requests = [];
  const waits = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).includes("integrate.api.nvidia.com")) {
      return Response.json({
        id: "completion-2",
        choices: [{ message: { role: "assistant", content: "ok" } }],
        usage: {
          prompt_tokens: 200,
          completion_tokens: 80,
          total_tokens: 280,
          prompt_tokens_details: { cached_tokens: 50 },
          completion_tokens_details: { reasoning_tokens: 40 },
        },
      });
    }
    if (String(url).endsWith("/runs")) return new Response(null, { status: 202 });
    throw new Error(`unexpected fetch: ${url}`);
  };
  try {
    await worker.queue(
      { messages: [message] },
      {
        NVIDIA_JOBS: kv,
        NVIDIA_RATE_LIMITER: limiterBinding(),
        LANGSMITH_API_KEY: "langsmith-secret",
        LANGSMITH_PROJECT: "team-iz-nvidia-usage",
      },
      { waitUntil(promise) { waits.push(promise); } }
    );
    await Promise.all(waits);
  } finally {
    globalThis.fetch = realFetch;
  }
  const trace = JSON.parse(requests.find((entry) => entry.url.endsWith("/runs")).options.body);
  assert.deepEqual(trace.outputs.usage_metadata.input_token_details, { cache_read: 50 });
  assert.deepEqual(trace.outputs.usage_metadata.output_token_details, { reasoning: 40 });
});

test("LangSmith usage metadata still emits all three token counts when NVIDIA omits completion_tokens", async () => {
  const kv = new MemoryKv();
  await kv.put("job-3", JSON.stringify({ status: "pending" }));
  const message = queueMessage({
    jobId: "job-3",
    apiKey: "nvapi-test-3",
    body: JSON.stringify({ model: "minimaxai/minimax-m3", messages: [{ role: "user", content: "hi" }] }),
    maxAttempts: 1,
  });
  const requests = [];
  const waits = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).includes("integrate.api.nvidia.com")) {
      return Response.json({
        id: "completion-3",
        choices: [{ message: { role: "assistant", content: "ok" } }],
        usage: { prompt_tokens: 100, total_tokens: 100 }, // no completion_tokens
      });
    }
    if (String(url).endsWith("/runs")) return new Response(null, { status: 202 });
    throw new Error(`unexpected fetch: ${url}`);
  };
  try {
    await worker.queue(
      { messages: [message] },
      {
        NVIDIA_JOBS: kv,
        NVIDIA_RATE_LIMITER: limiterBinding(),
        LANGSMITH_API_KEY: "langsmith-secret",
        LANGSMITH_PROJECT: "team-iz-nvidia-usage",
      },
      { waitUntil(promise) { waits.push(promise); } }
    );
    await Promise.all(waits);
  } finally {
    globalThis.fetch = realFetch;
  }
  const trace = JSON.parse(requests.find((entry) => entry.url.endsWith("/runs")).options.body);
  assert.equal(trace.outputs.usage_metadata.input_tokens, 100);
  assert.equal(trace.outputs.usage_metadata.output_tokens, 0);
  assert.equal(trace.outputs.usage_metadata.total_tokens, 100);
});

test("LANGSMITH_TAGS overrides the default tag list", async () => {
  const kv = new MemoryKv();
  await kv.put("job-4", JSON.stringify({ status: "pending" }));
  const message = queueMessage({
    jobId: "job-4",
    apiKey: "nvapi-test-4",
    body: JSON.stringify({ model: "minimaxai/minimax-m3", messages: [] }),
    maxAttempts: 1,
  });
  const requests = [];
  const waits = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).includes("integrate.api.nvidia.com")) {
      return Response.json({ id: "completion-4", choices: [{ message: { role: "assistant", content: "ok" } }] });
    }
    if (String(url).endsWith("/runs")) return new Response(null, { status: 202 });
    throw new Error(`unexpected fetch: ${url}`);
  };
  try {
    await worker.queue(
      { messages: [message] },
      {
        NVIDIA_JOBS: kv,
        NVIDIA_RATE_LIMITER: limiterBinding(),
        LANGSMITH_API_KEY: "langsmith-secret",
        LANGSMITH_PROJECT: "team-iz-nvidia-usage-code-qna",
        LANGSMITH_TAGS: "nvidia,code-qna,production",
      },
      { waitUntil(promise) { waits.push(promise); } }
    );
    await Promise.all(waits);
  } finally {
    globalThis.fetch = realFetch;
  }
  const trace = JSON.parse(requests.find((entry) => entry.url.endsWith("/runs")).options.body);
  assert.equal(trace.session_name, "team-iz-nvidia-usage-code-qna");
  assert.deepEqual(trace.tags, ["nvidia", "code-qna", "production"]);
});

test("terminal 429 is explicitly exposed to the orchestrator", async () => {
  const kv = new MemoryKv();
  await kv.put("job-429", JSON.stringify({ status: "pending" }));
  const message = queueMessage({
    jobId: "job-429",
    apiKey: "nvapi-test",
    body: JSON.stringify({ model: "minimaxai/minimax-m3", messages: [] }),
    maxAttempts: 1,
  });
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("too many", { status: 429 });
  try {
    await worker.queue(
      { messages: [message] },
      { NVIDIA_JOBS: kv, NVIDIA_RATE_LIMITER: limiterBinding() },
      { waitUntil() {} }
    );
  } finally {
    globalThis.fetch = realFetch;
  }
  const stored = JSON.parse(await kv.get("job-429"));
  assert.equal(stored.status, "error");
  assert.equal(stored.retryable, true);
  assert.equal(stored.rateLimited, true);
});
