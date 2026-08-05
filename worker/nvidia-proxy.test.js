import assert from "node:assert/strict";
import test from "node:test";

import worker from "./nvidia-proxy.js";

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

  // Minimal stand-in for KVNamespace#list(), added for the ?traffic=1 test below --
  // real Cloudflare KV returns { keys: [{name}, ...], list_complete, cursor }, but
  // nvidia-proxy.js only ever reads .keys[].name, so that's all this needs to provide.
  async list({ prefix = "" } = {}) {
    const keys = [...this.values.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name }));
    return { keys, list_complete: true, cursor: undefined };
  }
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
  const requests = [];
  const waits = [];
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
        LANGSMITH_API_KEY: "langsmith-secret",
        LANGSMITH_PROJECT: "team-iz-nvidia-usage-code-qna",
        LANGSMITH_TAGS: "nvidia,code-qna,production",
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
  assert.equal(trace.session_name, "team-iz-nvidia-usage-code-qna");
  assert.deepEqual(trace.tags, ["nvidia", "code-qna", "production"]);
  assert.equal(trace.extra.metadata.limiter_scope, "none");
  assert.equal(trace.extra.metadata.usage_metadata.input_tokens, 120);
  assert.equal(trace.extra.metadata.usage_metadata.output_tokens, 30);
  assert.equal(trace.extra.metadata.usage_metadata.total_tokens, 150);
  assert.equal(trace.outputs.usage_metadata.input_tokens, 120);
  assert.equal(trace.outputs.usage_metadata.output_tokens, 30);
  assert.equal(trace.outputs.usage_metadata.total_tokens, 150);
  assert.equal(trace.outputs.usage_metadata.input_cost, 0.0000288);
  assert.equal(trace.outputs.usage_metadata.output_cost, 0.0000288);
  assert.equal(trace.outputs.usage_metadata.total_cost, 0.0000576);
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
        LANGSMITH_API_KEY: "langsmith-secret",
        LANGSMITH_PROJECT: "team-iz-nvidia-usage-code-qna",
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

test("LangSmith project falls back to the unattributed default when the var is missing", async () => {
  const kv = new MemoryKv();
  await kv.put("job-3", JSON.stringify({ status: "pending" }));
  const message = queueMessage({
    jobId: "job-3",
    apiKey: "nvapi-test-3",
    body: JSON.stringify({ model: "minimaxai/minimax-m3", messages: [] }),
    maxAttempts: 1,
  });
  const requests = [];
  const waits = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).includes("integrate.api.nvidia.com")) {
      return Response.json({ id: "completion-3", choices: [{ message: { role: "assistant", content: "ok" } }] });
    }
    if (String(url).endsWith("/runs")) return new Response(null, { status: 202 });
    throw new Error(`unexpected fetch: ${url}`);
  };
  try {
    // Deliberately no LANGSMITH_PROJECT -- D6's fail-visible default.
    await worker.queue(
      { messages: [message] },
      { NVIDIA_JOBS: kv, LANGSMITH_API_KEY: "langsmith-secret" },
      { waitUntil(promise) { waits.push(promise); } }
    );
    await Promise.all(waits);
  } finally {
    globalThis.fetch = realFetch;
  }
  const trace = JSON.parse(requests.find((entry) => entry.url.endsWith("/runs")).options.body);
  assert.equal(trace.session_name, "team-iz-nvidia-usage-unattributed");
});

test("terminal 429 is marked retryable without a rate-limiter Durable Object", async () => {
  const kv = new MemoryKv();
  await kv.put("job-429", JSON.stringify({ status: "pending" }));
  const message = queueMessage({
    jobId: "job-429",
    apiKey: "nvapi-test",
    body: JSON.stringify({ model: "minimaxai/minimax-m3", messages: [] }),
    maxAttempts: 1,
  });
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("/runs")) return new Response(null, { status: 202 });
    return new Response("too many", { status: 429 });
  };
  try {
    await worker.queue(
      { messages: [message] },
      { NVIDIA_JOBS: kv },
      { waitUntil() {} }
    );
  } finally {
    globalThis.fetch = realFetch;
  }
  const stored = JSON.parse(await kv.get("job-429"));
  assert.equal(stored.status, "error");
  assert.equal(stored.retryable, true);
});

// D-fix (redteam audit H8, 2026-08-04): worker.fetch() (the HTTP entrypoint) had no
// coverage in this file at all -- every test above exercises worker.queue().
test("?traffic=1 requires x-nvidia-api-key, same gate as ?models=1", async () => {
  const kv = new MemoryKv();
  const env = { NVIDIA_JOBS: kv };

  const unauthenticated = await worker.fetch(new Request("https://proxy.internal/?traffic=1"), env);
  assert.equal(unauthenticated.status, 401);

  const authenticated = await worker.fetch(
    new Request("https://proxy.internal/?traffic=1", { headers: { "x-nvidia-api-key": "nvapi-test" } }),
    env
  );
  assert.equal(authenticated.status, 200);
  assert.deepEqual((await authenticated.json()).timestamps, []);
});
