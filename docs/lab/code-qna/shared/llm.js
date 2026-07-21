// D-C (PLAN.md): integrate.api.nvidia.com has no Access-Control-Allow-Origin header
// (verified 2026-07-14) -- direct browser calls are impossible, so every LLM call in
// this tool goes through a proxy the user configures (worker/nvidia-proxy.js is the
// reference implementation, deployable by anyone on the team). This file never talks
// to NVIDIA directly and never persists the key past the in-memory config object.
//
// D-H (2026-07-14, README D143): submits a job and polls for it instead of holding one
// HTTP request open. D-E's streaming attempt only helped once NVIDIA started sending
// bytes -- it couldn't help when NVIDIA was slow to send the FIRST byte, which turned out
// to be the actual failure mode (confirmed live: qwen3-next-80b took 92s on one call and
// didn't answer at all within 150s on another -- normal variance for this tier per
// feedback/nvidia_client.py's own 600s timeout and D98 history, not an outage). No
// client-facing request can survive that wait, streamed or not. So this doesn't try to --
// worker/nvidia-proxy.js now answers the POST almost instantly with a job_id and does the
// real call in a Cloudflare Queue consumer (up to 15 minutes, no client watching), and
// this file polls GET ?job=<id> until it's done. callPromptStage()/generateQuestion() etc.
// still just get back a plain complete string or parsed object -- this file is the only
// place that knows the transport is submit-and-poll now, same as it was the only place
// that knew about streaming before that.
//
// D-I (2026-07-14, worker/nvidia-proxy.js): the consumer now retries a failed attempt
// (up to MAX_ATTEMPTS=3, each with its own fresh 600s-per-attempt / 15min-per-invocation
// budget) instead of failing after one bad response -- see that file for why. MAX_POLL_MS
// below has to cover that whole worst case, not just one invocation, or this file gives up
// and shows an error while the job is still legitimately retrying server-side.
const LabLLM = (() => {
  const POLL_INTERVAL_MS = 3000;
  // Worst case on the server: 3 attempts * 600s + 2 retry delays * 5s =~ 1810s (~30min),
  // spread across separate queue-consumer invocations (see worker/nvidia-proxy.js D-I).
  // 35min gives headroom for Cloudflare's own redelivery scheduling latency on top of that.
  const MAX_POLL_MS = 35 * 60 * 1000;

  // D159 (2026-07-15): request timestamps for the debug traffic graph (docs/lab/
  // debug-traffic.js) -- "did this tab's own burst momentarily exceed NVIDIA's ~40rpm
  // free-tier ceiling" (nvidia-keypool-guard.py's own documented figure). Deliberately
  // just an in-memory array, capped and trimmed below -- this is a debugging aid, not a
  // metrics store, and it only sees requests THIS tab initiated (D156's parallel chunk
  // calls are the actual client-visible submissions; server-side retries inside
  // worker/nvidia-proxy.js's queue() happen invisibly to the browser and aren't counted
  // here -- a real limitation, not an oversight, noted so this graph isn't over-trusted
  // as the complete picture of load on the key).
  const requestLog = [];
  const REQUEST_LOG_MAX_AGE_MS = 15 * 60 * 1000; // trim anything older than the chart ever shows

  function recordRequest() {
    const now = Date.now();
    requestLog.push(now);
    const cutoff = now - REQUEST_LOG_MAX_AGE_MS;
    while (requestLog.length && requestLog[0] < cutoff) requestLog.shift();
  }

  function getRequestLog() {
    return requestLog.slice();
  }

  // D169 (2026-07-15): user asked to move retry ownership for P01's chunk-analysis burst
  // from the worker (each of up to 26 jobs independently deciding when to retry, per D-I)
  // to the client (this file + p01-runner.js), which can see all chunks' outcomes at once
  // and retry them together in coordinated, concurrency-capped rounds instead. opts.maxAttempts
  // is forwarded as the x-max-attempts header -- worker/nvidia-proxy.js falls back to its
  // existing MAX_ATTEMPTS=3 auto-retry when this header is absent, so P03 and P01's own
  // refine/question-gen calls (which never set it) are completely unaffected.
  async function submitAndPoll(proxyUrl, apiKey, body, opts = {}) {
    recordRequest();
    const headers = { "content-type": "application/json", "x-nvidia-api-key": apiKey };
    if (opts.maxAttempts) headers["x-max-attempts"] = String(opts.maxAttempts);
    const submitRes = await fetch(proxyUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    if (!submitRes.ok) {
      const text = await submitRes.text().catch(() => "");
      throw new Error(`작업 제출 실패 (HTTP ${submitRes.status}): ${text.slice(0, 300)}`);
    }
    const submitData = await submitRes.json();
    const jobId = submitData.job_id;
    if (!jobId) throw new Error(`작업 제출 응답에 job_id가 없음: ${JSON.stringify(submitData).slice(0, 200)}`);

    const base = proxyUrl.split("?")[0];
    const pollUrl = `${base}?job=${encodeURIComponent(jobId)}`;
    const startedAt = Date.now();
    while (Date.now() - startedAt < MAX_POLL_MS) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      let job;
      try {
        const pollRes = await fetch(pollUrl);
        if (!pollRes.ok) continue; // transient poll hiccup -- just try again next tick
        job = await pollRes.json();
      } catch (e) {
        continue; // network blip on the poll itself -- keep polling, don't fail the job for it
      }
      if (job.status === "pending") continue;
      if (job.status === "done") return JSON.parse(job.result);
      if (job.status === "error") {
        // D169: job.retryable (set by worker/nvidia-proxy.js) lets a caller that owns its
        // own retry decide whether resubmitting this exact job later is worth it, without
        // string-matching the error message.
        const err = new Error(`NVIDIA 호출 실패: ${job.error || "알 수 없는 오류"}`);
        err.retryable = !!job.retryable;
        throw err;
      }
      throw new Error(`알 수 없는 작업 상태: ${JSON.stringify(job).slice(0, 200)}`);
    }
    throw new Error(`작업이 ${Math.round(MAX_POLL_MS / 60000)}분 안에 끝나지 않음 (job_id=${jobId})`);
  }

  async function chatJSON({ model, messages, maxTokens, temperature = 0.0, jsonMode = true, maxAttempts }) {
    const proxyUrl = LabConfig.get("proxy-url");
    const apiKey = LabConfig.get("nvidia-key");
    if (!proxyUrl || !apiKey) {
      throw new Error("NVIDIA API 키와 프록시 URL을 먼저 입력하세요 (상단 연결 설정).");
    }
    const body = { model, messages, max_tokens: maxTokens, temperature };
    if (jsonMode) body.response_format = { type: "json_object" };
    const data = await submitAndPoll(proxyUrl, apiKey, body, { maxAttempts });
    const choice = data.choices && data.choices[0] && data.choices[0].message;
    if (!choice) throw new Error(`예상치 못한 응답 형태: ${JSON.stringify(data).slice(0, 300)}`);
    // D131/D142's fallback chain, ported from the real pipeline -- some models (step-3.5-flash)
    // put their actual JSON-mode answer in reasoning_content, leaving content null/empty.
    const resolved = choice.content || choice.reasoning_content;
    const finishReason = data.choices[0].finish_reason;
    if (!resolved) {
      throw new Error(`빈 응답 (content 없음, finish_reason=${finishReason})`);
    }
    // D158 (2026-07-15): finishReason now always returned, not just logged on the empty-
    // content path -- a real 251-page run had 2/26 chunks fail JSON parsing with "Expected
    // ',' or ']' after array element", the textbook signature of a response cut off
    // mid-array. Without finish_reason, that was a guess; callers can now check
    // finishReason === "length" and know for certain rather than infer from parse errors.
    return { role: "assistant", content: resolved, finishReason };
  }

  // D181: maxAttempts wasn't threaded through here even though submitAndPoll/the worker
  // have supported it since D169 -- chatJSON (P01's chunk-analysis) was the only caller
  // that ever needed it before now. P03's interview calls all go through this function, so
  // without this they had no way to opt into more retry budget under elevated shared
  // traffic (see debug-traffic.js's getCurrentRate(), used by p03-runner.js).
  async function chatTool({ model, messages, tool, maxTokens, temperature = 0.0, maxAttempts }) {
    const proxyUrl = LabConfig.get("proxy-url");
    const apiKey = LabConfig.get("nvidia-key");
    if (!proxyUrl || !apiKey) {
      throw new Error("NVIDIA API 키와 프록시 URL을 먼저 입력하세요 (상단 연결 설정).");
    }
    const body = {
      model, messages, max_tokens: maxTokens, temperature,
      tools: [{ type: "function", function: { name: tool.name, description: tool.description, parameters: tool.input_schema } }],
      tool_choice: { type: "function", function: { name: tool.name } },
    };
    const data = await submitAndPoll(proxyUrl, apiKey, body, { maxAttempts });
    const choice = data.choices && data.choices[0] && data.choices[0].message;
    const call = choice && choice.tool_calls && choice.tool_calls.find((c) => c.function.name === tool.name);
    if (!call) throw new Error(`tool_calls에서 ${tool.name}을 찾지 못함: ${JSON.stringify(data).slice(0, 300)}`);
    return JSON.parse(call.function.arguments);
  }

  // D200: P03's L2/L3/Reflection follow-up questions used to be pure completions against a
  // static code snippet chosen once at session start -- never re-verified against the real
  // repo mid-interview (the gap exposed by comparing against a reference "grounded
  // fact-check" interview transcript; user directed adopting the high-cost structural fix).
  // chatTool() above is a genuine dead end for this: it forces `tool_choice` to exactly one
  // function and returns after a single round trip, so a model that needs to read a file
  // before it can ask a grounded question has nowhere to put that request. This is the
  // first multi-turn tool-calling primitive in this codebase.
  //   WHY: send ALL candidate tools with `tool_choice: "auto"` so the model can choose
  //   between calling a non-terminal tool (executed locally, result fed back into
  //   `messages` per the OpenAI tool-calling message convention: the assistant's
  //   tool-call message followed by a role:"tool" message carrying that call's id) or
  //   calling `terminalToolName` to finish -- exactly mirroring chatTool()'s return shape
  //   once it does.
  //   COST: up to maxRounds+1 full submitAndPoll round trips (each a real job-queue
  //   submit+poll cycle, not instant) instead of chatTool()'s always-1. Callers must budget
  //   for this explicitly (see p03-engine.js's FACT_CHECK_MAX_ROUNDS comment for how P03
  //   bounds it: fact-check only on the first dedup attempt, not every retry).
  //   EXIT: if a model ever returns >1 tool_calls in one round, only the first is acted on
  //   (documented v1 scope limit, not a bug) -- if that turns out to matter, execute all
  //   calls in the response and push one role:"tool" message per call before re-polling.
  //
  // D204: user-reported gap -- `tool_choice: "auto"` let a model skip list_files/read_file
  // entirely and go straight to the terminal tool, making "grounded fact-check" probabilistic
  // instead of guaranteed. New `minNonTerminalRounds` (default 0, fully backward compatible
  // for every existing call site) makes at least that many non-terminal tool calls
  // structurally mandatory:
  //   WHY: instead of relying on `tool_choice: "required"` (unverified whether every
  //   NVIDIA-proxied model honors it), the terminal tool is simply REMOVED from the tools
  //   list for any round where the floor hasn't been met yet -- the model cannot choose what
  //   isn't offered, so this works regardless of provider-specific tool_choice semantics.
  //   COST: a model that truly cannot use tools this round gets re-nudged (plain user
  //   message) up to MAX_MANDATORY_STALL_RETRIES times before the floor is abandoned and the
  //   loop proceeds anyway -- never blocks the interview forever, matching D200's
  //   graceful-degradation stance elsewhere in this file.
  //   EXIT: if a specific model reliably stalls even after the nudge, that model may need a
  //   different mandatory-round strategy (e.g. tool_choice: "required" first, this as
  //   fallback) -- revisit MAX_MANDATORY_STALL_RETRIES/the nudge text then.
  const MAX_MANDATORY_STALL_RETRIES = 2;

  async function chatToolLoop({ model, messages, tools, executors, terminalToolName,
                                 maxRounds = 2, minNonTerminalRounds = 0, maxTokens, temperature = 0.0, maxAttempts, onProgress }) {
    const proxyUrl = LabConfig.get("proxy-url");
    const apiKey = LabConfig.get("nvidia-key");
    if (!proxyUrl || !apiKey) {
      throw new Error("NVIDIA API 키와 프록시 URL을 먼저 입력하세요 (상단 연결 설정).");
    }
    const toolDefs = tools.map((t) => ({ type: "function", function: { name: t.name, description: t.description, parameters: t.input_schema } }));
    const terminalDef = toolDefs.find((t) => t.function.name === terminalToolName);
    if (!terminalDef) throw new Error(`chatToolLoop: terminalToolName "${terminalToolName}"이 tools 목록에 없음`);
    const nonTerminalDefs = toolDefs.filter((t) => t.function.name !== terminalToolName);
    if (minNonTerminalRounds > 0 && !nonTerminalDefs.length) {
      throw new Error("chatToolLoop: minNonTerminalRounds>0인데 non-terminal tool이 없음");
    }

    let round = 0;
    let nonTerminalCallsMade = 0;
    let mandatoryStallRetries = 0;
    while (true) {
      // D200: once `round >= maxRounds`, force tool_choice back to ONLY the terminal tool --
      // guarantees the loop terminates within a bounded number of round trips regardless of
      // model behavior, reusing chatTool()'s exact forced-single-tool shape as the fallback.
      const forced = round >= maxRounds;
      // D204: the mandatory floor never extends the round budget above -- it only restricts
      // what's offered within it.
      const mustCallNonTerminal = !forced && nonTerminalCallsMade < minNonTerminalRounds;
      const body = {
        model, messages, max_tokens: maxTokens, temperature,
        tools: forced ? [terminalDef] : mustCallNonTerminal ? nonTerminalDefs : toolDefs,
        tool_choice: forced ? { type: "function", function: { name: terminalToolName } } : "auto",
      };
      const data = await submitAndPoll(proxyUrl, apiKey, body, { maxAttempts });
      const choice = data.choices && data.choices[0] && data.choices[0].message;
      const calls = (choice && choice.tool_calls) || [];

      if (!calls.length) {
        if (forced) throw new Error(`chatToolLoop: 강제 종료 라운드에서도 tool_calls 없음: ${JSON.stringify(data).slice(0, 300)}`);
        if (mustCallNonTerminal) {
          mandatoryStallRetries += 1;
          if (mandatoryStallRetries > MAX_MANDATORY_STALL_RETRIES) {
            // D204: give up enforcing the floor rather than block the interview forever --
            // this model just isn't going to call a tool here no matter how it's asked.
            if (onProgress) onProgress(`⚠ 도구 호출을 요청했지만 모델이 응답하지 않아 강제를 포기하고 진행합니다`);
            minNonTerminalRounds = nonTerminalCallsMade;
            continue;
          }
          messages.push({ role: "user", content: "반드시 list_files 또는 read_file 중 하나를 먼저 호출하세요. 텍스트로 직접 답하지 마세요." });
          continue;
        }
        // Model responded with plain text instead of calling any tool under "auto" -- not
        // expected, but not fatal: record what it said and force terminal-only on the next
        // round instead of throwing (guaranteed termination still holds).
        messages.push({ role: "assistant", content: choice ? (choice.content || "") : "" });
        round = maxRounds;
        continue;
      }

      const call = calls[0]; // v1 scope: acts on the first tool_call only, see file header
      if (call.function.name === terminalToolName) {
        return JSON.parse(call.function.arguments);
      }

      const executor = executors[call.function.name];
      if (!executor) throw new Error(`chatToolLoop: executor 없음: ${call.function.name}`);
      if (onProgress) onProgress(`⚙ ${call.function.name} 호출 중...`);
      // Deliberately not caught here -- a tool executor throwing (e.g. a GitHub rate-limit
      // error) is the caller's decision to handle (fall back, warn, etc.), not this generic
      // primitive's. See p03-engine.js's generateQuestion() for the fallback this enables.
      const args = JSON.parse(call.function.arguments);
      const toolResult = await executor(args);

      messages.push({ role: "assistant", content: choice.content || null, tool_calls: [call] });
      messages.push({ role: "tool", tool_call_id: call.id, content: JSON.stringify(toolResult) });
      round += 1;
      nonTerminalCallsMade += 1;
    }
  }

  function extractJsonObject(text) {
    let cleaned = (text || "").trim();
    cleaned = cleaned.replace(/^```(?:json)?\s*/, "").replace(/\s*```$/, "");
    try {
      return JSON.parse(cleaned);
    } catch (e) {
      const m = cleaned.match(/\{[\s\S]*\}/);
      if (!m) throw e;
      return JSON.parse(m[0]);
    }
  }

  return { chatJSON, chatTool, chatToolLoop, extractJsonObject, getRequestLog };
})();
