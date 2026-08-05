// shared/llm.js::chatToolLoop()의 강제-종료 라운드(forced) 보장 회귀 테스트
// (redteam audit M6, 2026-08-05).
//   실행: node --test tests/js/llm-chat-tool-loop.test.js   (저장소 루트에서)
//
// D200 주석의 약속: "guarantees the loop terminates within a bounded number of round
// trips regardless of model behavior" -- 그런데 forced 라운드는 tools를 [terminalDef]로
// 좁히고 tool_choice를 강제할 뿐, 모델이 그 강제를 실제로 따르는지는 검증하지 않았다.
// 비순응 모델이 forced 라운드에서도 non-terminal tool을 호출하면 그대로 실행되고
// round가 maxRounds를 넘어가 버려, 위 약속이 깨진다. submitAndPoll()은 3초 간격으로
// 폴링하므로(POLL_INTERVAL_MS) 매 라운드 실제로 한 번의 폴 대기가 걸린다 -- 아래 fake
// fetch는 매 제출을 첫 폴에서 바로 done으로 답해 라운드당 대기를 최소화한다.
const test = require("node:test");
const assert = require("node:assert");

globalThis.LabConfig = {
  get: (key) => ({ "proxy-url": "https://proxy.example.workers.dev/", "nvidia-key": "test-key" }[key]),
};
const LabLLM = require("../../shared/llm.js");

function toolCallResponse(name, args) {
  return { choices: [{ message: { content: null, tool_calls: [{ id: `call_${name}`, function: { name, arguments: JSON.stringify(args) } }] } }] };
}

// submitRes = await fetch(proxyUrl, {method:"POST", ...}); pollRes = await fetch(pollUrl)
// (no opts) -- distinguishes submit vs poll the same way submitAndPoll() itself does.
function makeFakeFetch(responses) {
  const results = new Map();
  const fn = async (url, opts) => {
    if (opts && opts.method === "POST") {
      const jobId = `job-${fn.submitCount}`;
      results.set(jobId, responses[fn.submitCount]);
      fn.submitCount += 1;
      return { ok: true, json: async () => ({ job_id: jobId }) };
    }
    const jobId = new URL(url).searchParams.get("job");
    return { ok: true, json: async () => ({ status: "done", result: JSON.stringify(results.get(jobId)) }) };
  };
  fn.submitCount = 0;
  return fn;
}

const TOOLS = [
  { name: "list_files", description: "list files", input_schema: { type: "object", properties: {} } },
  { name: "answer", description: "final answer", input_schema: { type: "object", properties: { text: { type: "string" } } } },
];

test("forced round: a non-compliant non-terminal tool call throws instead of executing", async () => {
  const listFilesCalls = [];
  const fakeFetch = makeFakeFetch([
    toolCallResponse("list_files", {}), // round 0, not forced (0 >= 1 is false)
    toolCallResponse("list_files", {}), // round 1, forced (1 >= 1) -- model ignores tool_choice
    toolCallResponse("answer", { text: "should never be reached" }), // round 2 -- must never be consumed
  ]);
  const realFetch = globalThis.fetch;
  globalThis.fetch = fakeFetch;
  try {
    await assert.rejects(
      () => LabLLM.chatToolLoop({
        model: "test-model",
        messages: [{ role: "user", content: "go" }],
        tools: TOOLS,
        executors: { list_files: async () => { listFilesCalls.push(1); return { files: ["a.py"] }; } },
        terminalToolName: "answer",
        maxRounds: 1,
      }),
      /강제 종료 라운드에서 non-terminal tool 호출: list_files/
    );
    assert.strictEqual(fakeFetch.submitCount, 2, "round 2의 response는 절대 제출되면 안 된다");
    assert.strictEqual(listFilesCalls.length, 1, "forced 라운드의 list_files는 실행되지 않아야 한다(round 0의 1회만)");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("forced round: a compliant terminal call still returns normally", async () => {
  const fakeFetch = makeFakeFetch([toolCallResponse("answer", { text: "done" })]);
  const realFetch = globalThis.fetch;
  globalThis.fetch = fakeFetch;
  try {
    const result = await LabLLM.chatToolLoop({
      model: "test-model",
      messages: [{ role: "user", content: "go" }],
      tools: TOOLS,
      executors: {},
      terminalToolName: "answer",
      maxRounds: 0, // round 0 is immediately forced (0 >= 0)
    });
    assert.deepStrictEqual(result, { text: "done" });
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("non-forced round: a legitimate non-terminal call still executes and proceeds", async () => {
  const listFilesCalls = [];
  const fakeFetch = makeFakeFetch([
    toolCallResponse("list_files", {}), // round 0, not forced (0 >= 5 is false)
    toolCallResponse("answer", { text: "final" }), // round 1, still not forced (1 >= 5)
  ]);
  const realFetch = globalThis.fetch;
  globalThis.fetch = fakeFetch;
  try {
    const result = await LabLLM.chatToolLoop({
      model: "test-model",
      messages: [{ role: "user", content: "go" }],
      tools: TOOLS,
      executors: { list_files: async () => { listFilesCalls.push(1); return { files: ["a.py"] }; } },
      terminalToolName: "answer",
      maxRounds: 5,
    });
    assert.deepStrictEqual(result, { text: "final" });
    assert.strictEqual(listFilesCalls.length, 1, "정상 경로의 non-terminal 호출은 그대로 실행돼야 한다");
  } finally {
    globalThis.fetch = realFetch;
  }
});
