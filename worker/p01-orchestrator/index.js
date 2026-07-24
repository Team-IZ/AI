// D-P01ORCH: server-side port of curriculum-manager's P01 chunk-analysis orchestration
// (previously entirely in-browser via docs/lab/p01-runner.js's run()). See
// wrangler.toml's header comment for the WHY/COST/EXIT of this file existing at all.
//
// SCOPE NOTE: curriculum-manager always calls run() with {skipRefine:true,
// skipQuestionGen:true} (confirmed live in docs/lab/curriculum-manager/index.html) --
// audits/fixLog/questions are always [] for this caller, so only the chunk-analysis
// stage ("p01-2") ever makes an NVIDIA call here. This file does NOT port refine or
// question-generation at all -- if curriculum-manager ever starts using them, this file
// needs new stages ported in, not a bug in what's here.
//
// EVERY constant/function below is a deliberate line-for-line port of
// docs/lab/p01-runner.js and docs/lab/llm.js, cited by name so a future edit to either
// original file can be checked against this copy. p01-runner.js/llm.js/db.js themselves
// are never modified by this file's existence -- the original Pipeline Lab P01 tab keeps
// using them completely unaffected.

import { DurableObject } from "cloudflare:workers";

// ---- ported from p01-runner.js (constants) ----
const CHUNK_CONCURRENCY = 40; // p01-runner.js:54
const MAX_RETRY_ROUNDS = 3; // p01-runner.js:79
const ROUND_RETRY_DELAY_MS = 60_000; // p01-runner.js:80
const MAX_LENGTH_DOUBLINGS = 2; // p01-runner.js:470

// ---- ported from llm.js ----
const POLL_INTERVAL_MS = 3000; // llm.js:27
const MAX_POLL_MS = 35 * 60 * 1000; // llm.js:31
const RETRYABLE_BODY_ERROR_CODES = new Set([429, 500, 502, 503, 524]); // llm.js:156
const REASONING_EFFORT_BY_MODEL = { "stepfun-ai/step-3.7-flash": "low" }; // llm.js:133-135

// ---- ported from config.js (D-D's own comment: Supabase URL/anon key are the team's
// single shared DB, meant to ship in client code -- RLS, not secrecy, protects the
// data -- so embedding the same public values here carries no new exposure) ----
const SUPABASE_URL = "https://oziaeqcvrkrqkhwrybfj.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im96aWFlcWN2cmtycWtod3J5YmZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQwMDA4MTksImV4cCI6MjA5OTU3NjgxOX0.hBgzs0V7Nw3WLB8_zNuPDfluYrqOH2_Dto1weQF5iKo";

// D-cors-array: two legitimate origins serve curriculum-manager today (main repo's own
// Pages deploy + the Team-IZ mirror) -- worker/nvidia-proxy.js's own D-fix15 EXIT note
// anticipated needing exactly this ("if a second legitimate origin needs access... an
// array-aware check"). Reflects back whichever of these the request's Origin matches;
// anything else gets no CORS header at all (browser blocks it client-side).
const ALLOWED_ORIGINS = ["https://popixoxipop-collab.github.io", "https://team-iz.github.io"];

// ---- ported from prompt_manifest.json (manifest_version "1.0.0" at time of writing --
// curriculum-manager never calls LabApp.setOverride (grep-confirmed empty), so the raw
// manifest defaults, not any override, are what run() actually used) ----
const P01_2_STAGE = {
  system: "You are a precise curriculum-analysis extractor. Output strict JSON only.",
  user_template: `KT AIVLE School {course_label} curriculum PDF page range: {chunk_range}.

Return ONLY valid JSON with this exact shape:
{{
  "chunk_range": "{chunk_range}",
  "units": [{{"unit_id": "02", "unit_title": "<specific topic on these pages, e.g. Variables and Data Types>", "source_pages": [4, 5]}}],
  "concepts": [
    {{
      "name": "short concept name",
      "kind": "concept|code_example|caution",
      "unit_id": "<must match one of the unit_id values in units above>",
      "summary": "one sentence grounded in the slides",
      "source_pages": [1],
      "evidence": "short paraphrase of the page evidence"
    }}
  ]
}}

Rules:
- unit_title must name the actual topic on these pages (e.g. "Variables and Data Types", "Loops", "Exception Handling") -- never a generic label like "Overview", "Introduction", or "Chapter N" unless these exact pages are a title/table-of-contents slide with no substantive teaching content.
- If this chunk covers more than one distinct topic, list each as its own entry in units, and set each concept's unit_id to whichever unit it actually belongs to.
- Every concept's unit_id must exactly match one of the unit_id values listed in units above.
- Every concept must have at least one concrete page number from {chunk_start}..{chunk_end}.
- Do not invent content outside the given pages.
- If a page is just title/table-of-contents, preserve it only if it affects unit mapping.

PDF text:
{chunk_text}`,
  max_tokens: 3600,
  temperature: 0.0,
};
const JSON_REPAIR_STAGE = {
  system: "You repair malformed JSON. Output strict JSON only.",
  user_template:
    "Repair the following malformed JSON into one valid JSON object. Preserve all fields and content where possible. Return JSON only.\n\n{malformed_content}",
};

// ---- ported from app.js:234-236 (fillTemplate) ----
function fillTemplate(template, values) {
  return template.replace(/\{(\w+)\}/g, (m, key) => (key in values ? String(values[key]) : m));
}

// ---- ported from llm.js:339-349 (extractJsonObject) ----
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

function markRetryableFromBody(err, data) {
  // llm.js:157-160
  err.retryable = Boolean(data && data.error && RETRYABLE_BODY_ERROR_CODES.has(data.error.code));
  return err;
}

// ---- ported from llm.js:63-106 (submitAndPoll) -- talks to the EXISTING
// worker/nvidia-proxy.js over plain HTTPS, same contract the browser used to call
// directly. nvidia-proxy.js itself needs zero changes for this. ----
async function submitAndPoll(proxyUrl, apiKey, body, opts = {}) {
  const headers = { "content-type": "application/json", "x-nvidia-api-key": apiKey };
  if (opts.maxAttempts) headers["x-max-attempts"] = String(opts.maxAttempts);
  const submitRes = await fetch(proxyUrl, { method: "POST", headers, body: JSON.stringify(body) });
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
      if (!pollRes.ok) continue;
      job = await pollRes.json();
    } catch (e) {
      continue;
    }
    if (job.status === "pending") continue;
    if (job.status === "done") return JSON.parse(job.result);
    if (job.status === "error") {
      const err = new Error(`NVIDIA 호출 실패: ${job.error || "알 수 없는 오류"}`);
      err.retryable = !!job.retryable;
      throw err;
    }
    throw new Error(`알 수 없는 작업 상태: ${JSON.stringify(job).slice(0, 200)}`);
  }
  throw new Error(`작업이 ${Math.round(MAX_POLL_MS / 60000)}분 안에 끝나지 않음 (job_id=${jobId})`);
}

// ---- ported from llm.js:162-188 (chatJSON) ----
async function chatJSON({ proxyUrl, apiKey, model, messages, maxTokens, temperature = 0.0, maxAttempts }) {
  const body = { model, messages, max_tokens: maxTokens, temperature, response_format: { type: "json_object" } };
  const reasoningEffort = REASONING_EFFORT_BY_MODEL[model];
  if (reasoningEffort) body.reasoning_effort = reasoningEffort;
  const data = await submitAndPoll(proxyUrl, apiKey, body, { maxAttempts });
  const choice = data.choices && data.choices[0] && data.choices[0].message;
  if (!choice) throw markRetryableFromBody(new Error(`예상치 못한 응답 형태: ${JSON.stringify(data).slice(0, 300)}`), data);
  const resolved = choice.content || choice.reasoning_content;
  const finishReason = data.choices[0].finish_reason;
  if (!resolved) throw new Error(`빈 응답 (content 없음, finish_reason=${finishReason})`);
  return { content: resolved, finishReason };
}

// ---- ported from p01-runner.js:475-512 (callPromptStage), narrowed to the one stage
// ("p01-2") curriculum-manager ever calls, plus the json-repair fallback it can invoke ----
async function callChunkAnalysis({ proxyUrl, apiKey, model, courseLabel, chunk, maxAttempts }) {
  const stage = P01_2_STAGE;
  const values = { course_label: courseLabel, chunk_range: chunk.range, chunk_start: chunk.start, chunk_end: chunk.end, chunk_text: chunk.text.slice(0, 18000) };
  const system = stage.system;
  const userMsg = fillTemplate(stage.user_template, values);
  const messages = [{ role: "system", content: system }, { role: "user", content: userMsg }];
  let maxTokens = stage.max_tokens;

  let choice = await chatJSON({ proxyUrl, apiKey, model, messages, maxTokens, maxAttempts });
  try {
    return extractJsonObject(choice.content);
  } catch (e) {
    if (choice.finishReason === "length") {
      for (let attempt = 1; attempt <= MAX_LENGTH_DOUBLINGS; attempt++) {
        maxTokens *= 2;
        choice = await chatJSON({ proxyUrl, apiKey, model, messages, maxTokens, maxAttempts });
        try {
          return extractJsonObject(choice.content);
        } catch (e2) {
          if (choice.finishReason !== "length") {
            throw new Error(`응답 잘림 재시도 중(max_tokens ${maxTokens}) 다른 사유로 파싱 실패: ${e2.message}`);
          }
        }
      }
      throw new Error(`응답이 계속 잘림(finish_reason=length) → max_tokens ${maxTokens}까지 올려도 여전히 파싱 실패`);
    }
    const repairMsg = fillTemplate(JSON_REPAIR_STAGE.user_template, { malformed_content: (choice.content || "").slice(0, 14000) });
    const repaired = await chatJSON({
      proxyUrl, apiKey, model,
      messages: [{ role: "system", content: JSON_REPAIR_STAGE.system }, { role: "user", content: repairMsg }],
      maxTokens: stage.max_tokens, maxAttempts,
    });
    return extractJsonObject(repaired.content);
  }
}

// ---- ported from p01-runner.js:172-201 (makeUnitMap) ----
function makeUnitMap(chunkResults) {
  const unitMap = {};
  for (const chunk of chunkResults) {
    for (const unit of chunk.units || []) {
      const unitId = String(unit.unit_id || "unknown");
      if (!unitMap[unitId]) {
        unitMap[unitId] = { unit_id: unitId, unit_title: unit.unit_title || "", source_pages: [], concepts: [], code_examples: [], cautions: [] };
      }
      unitMap[unitId].source_pages.push(...(unit.source_pages || []));
    }
    const chunkUnitIds = new Set((chunk.units || []).map((u) => String(u.unit_id || "unknown")));
    const fallbackUnit = (chunk.units && chunk.units[0] && String(chunk.units[0].unit_id)) || "unknown";
    for (const concept of chunk.concepts || []) {
      const declaredUnit = concept.unit_id !== undefined && concept.unit_id !== null ? String(concept.unit_id) : null;
      const targetUnit = declaredUnit && chunkUnitIds.has(declaredUnit) ? declaredUnit : fallbackUnit;
      if (!unitMap[targetUnit]) continue;
      const item = { name: concept.name || "unnamed", summary: concept.summary || "", evidence: concept.evidence || "", source_pages: concept.source_pages || [], chunk_range: chunk.chunk_range };
      const kind = concept.kind || "concept";
      const bucket = kind === "code_example" ? "code_examples" : kind === "caution" ? "cautions" : "concepts";
      unitMap[targetUnit][bucket].push(item);
    }
  }
  for (const u of Object.values(unitMap)) u.source_pages = [...new Set(u.source_pages)].sort((a, b) => a - b);
  return unitMap;
}

// ---- ported from p01-runner.js:216-246 (normalizeUnitMap) -- pipelineId/LabApp.log
// dropped (nothing to log to server-side; progress is written to DO storage instead) ----
function normalizeUnitMap(unitMap) {
  const normalized = {};
  for (const [unitId, unit] of Object.entries(unitMap)) {
    const dedupeItems = (items) => {
      const seen = new Set();
      const result = [];
      for (const item of items || []) {
        const pages = [...new Set(item.source_pages || [])].sort((a, b) => a - b);
        if (!pages.length) continue;
        const key = `${item.name || ""}::${pages.join(",")}`;
        if (seen.has(key)) continue;
        seen.add(key);
        result.push({ ...item, source_pages: pages });
      }
      return result;
    };
    normalized[unitId] = {
      ...unit,
      source_pages: [...new Set(unit.source_pages || [])].sort((a, b) => a - b),
      concepts: dedupeItems(unit.concepts),
      code_examples: dedupeItems(unit.code_examples),
      cautions: dedupeItems(unit.cautions),
    };
  }
  return normalized;
}

// ---- ported from p01-runner.js:392-446 (buildGraph) -- audits is always [] here
// (curriculum-manager always sets skipRefine:true, confirmed in index.html), the
// function already treats that as optional ((audits || []).forEach(...)) ----
function buildGraph(unitMap, audits) {
  const nodes = [];
  const links = [];
  const seen = new Set();
  function addNode(id, label, type, attrs) {
    if (seen.has(id)) return;
    seen.add(id);
    nodes.push({ id, label, type, ...(attrs || {}) });
  }
  function addLink(source, target, relation, attrs) {
    links.push({ source, target, relation, ...(attrs || {}) });
  }
  addNode("doc:curriculum", "curriculum", "document");
  for (const [unitId, unit] of Object.entries(unitMap)) {
    const uid = `unit:${unitId}`;
    addNode(uid, `Unit ${unitId} ${unit.unit_title || ""}`.trim(), "unit", { source_pages: unit.source_pages || [] });
    addLink("doc:curriculum", uid, "contains_unit");
    for (const [group, relation] of [["concepts", "teaches"], ["code_examples", "shows_code"], ["cautions", "warns"]]) {
      (unit[group] || []).forEach((item, idx) => {
        const cid = `${group}:${unitId}:${idx + 1}`;
        const pages = item.source_pages || [];
        addNode(cid, item.name || cid, group.endsWith("s") ? group.slice(0, -1) : group, {
          summary: item.summary || "", evidence: item.evidence || "", source_pages: pages, chunk_range: item.chunk_range || "",
        });
        addLink(uid, cid, relation);
        for (const page of pages) {
          const pid = `page:${page}`;
          addNode(pid, `p${page}`, "page", { page });
          addLink(cid, pid, "sourced_by");
        }
      });
    }
  }
  (audits || []).forEach((audit, idx) => {
    const aid = `audit:${audit.iteration || idx + 1}`;
    addNode(aid, `refine iteration ${audit.iteration}`, "refine_audit", { status: audit.status });
    addLink(aid, "doc:curriculum", "audits");
    (audit.issues || []).forEach((issue, iidx) => {
      const iid = `${aid}:issue:${iidx + 1}`;
      const pages = issue.source_pages || [];
      addNode(iid, issue.issue || iid, "refine_issue", { severity: issue.severity, source_pages: pages });
      addLink(aid, iid, "found_issue");
      for (const page of pages) {
        const pid = `page:${page}`;
        addNode(pid, `p${page}`, "page", { page });
        addLink(iid, pid, "issue_page");
      }
    });
  });
  return { directed: true, multigraph: false, graph: { name: "curriculum_page_grounded_graph", schema: "graphify-compatible node-link" }, nodes, links };
}

// ---- Supabase PostgREST helpers. Uses the SUBMITTING USER's own access token (captured
// client-side at submit time, same technique docs/lab/db.js's armAbandonBeacon already
// uses for exactly this reason -- pdf_analysis.runs/artifacts RLS requires
// member_id = auth.uid(), so a service-role bypass would be the wrong tool here even if
// it were provisioned) as the Bearer token, with the team anon key as apikey.
//
// D-fix (found live during verification): schema selection in PostgREST is NOT a URL
// path prefix ("pdf_analysis.runs" as a path segment 404s -- confirmed live, PostgREST
// read it as table literally named "pdf_analysis.runs" in the public schema). It's the
// Accept-Profile (GET) / Content-Profile (write) HTTP header -- same mechanism
// docs/lab/labdb-shim.js's ensureClient({db:{schema:"pdf_analysis"}}) hides behind
// supabase-js. Table names below are bare ("runs"/"artifacts"). ----
async function pgFetch(accessToken, path, init = {}) {
  const isWrite = init.method === "PATCH" || init.method === "POST" || init.method === "DELETE";
  const res = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: SUPABASE_ANON_KEY,
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      [isWrite ? "Content-Profile" : "Accept-Profile"]: "pdf_analysis",
      Prefer: isWrite ? "return=representation" : undefined,
      ...(init.headers || {}),
    },
  });
  const text = await res.text().catch(() => "");
  if (!res.ok) {
    throw new Error(`Supabase ${init.method || "GET"} ${path} 실패 (HTTP ${res.status}): ${text.slice(0, 300)}`);
  }
  // D-fix: don't assume only 204 means "no body" -- PostgREST can 2xx with an empty
  // body for other reasons too (e.g. Prefer header composition mistakes, see
  // upsertArtifact's own D-fix note), and JSON.parse("") throws. Empty text -> null.
  if (!text) return null;
  return JSON.parse(text);
}

// D-fix (found live during verification): pdf_analysis.runs.member_id is `not null`
// with NO column default -- db.js's own saveRun()/startRun() always set it explicitly
// from c.auth.getUser()'s id (see db.js:95-99,159-163). This Worker never calls that;
// reading the `sub` claim straight out of the access token (a standard JWT claim, same
// value auth.uid() resolves to server-side) is equivalent and needs no extra round trip.
// Not signature-verified here on purpose: RLS re-derives auth.uid() from the SAME token
// server-side and will reject the insert if member_id doesn't match, so a forged/altered
// token can't insert under someone else's identity even if this decode were tricked --
// it can only make this Worker's OWN insert fail the same RLS check Supabase enforces.
function decodeJwtSub(token) {
  const payload = token.split(".")[1];
  const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
  return JSON.parse(json).sub;
}

// mirrors db.js:157-172 (startRun), status "queued" instead of "running" -- this is the
// row that makes an in-progress analysis representable server-side at all (the gap
// p01-runner.js's run() never had, since it only ever wrote to DB once, at the end).
async function insertQueuedRun(accessToken, { model, input_meta }) {
  const memberId = decodeJwtSub(accessToken);
  const rows = await pgFetch(accessToken, "runs", {
    method: "POST",
    body: JSON.stringify({ member_id: memberId, pipeline: "p01", model, status: "queued", input_meta: input_meta || {}, overrides: {} }),
  });
  return rows[0];
}

async function patchRun(accessToken, runId, patch, extraFilter = "") {
  const rows = await pgFetch(accessToken, `runs?id=eq.${encodeURIComponent(runId)}${extraFilter}`, { method: "PATCH", body: JSON.stringify(patch) });
  return rows;
}

// D-fix (security review, post-verification): proves the caller is allowed to READ this
// run by reusing the same "runs read all" RLS any authenticated team member already gets
// via the normal Supabase client -- rejects (throws) only for a token that isn't even a
// valid authenticated session, not owner-only (matching the DB's own visibility model).
async function verifyReadAccess(accessToken, runId) {
  await pgFetch(accessToken, `runs?id=eq.${encodeURIComponent(runId)}&select=id`, { method: "GET" });
}

// mirrors db.js:133-138's artifacts insert, but upsert (onConflict run_id,kind) so
// progressive unit_map writes during the run don't create duplicate rows -- relies on
// the pdf_analysis_artifacts_run_kind_unique constraint added alongside this feature.
//
// D-fix (found live during verification): this Prefer header used to be just
// "resolution=merge-duplicates", which REPLACES (not adds to) pgFetch's own default
// "return=representation" Prefer header -- PostgREST then defaults to return=minimal,
// responding 201 with an EMPTY body. pgFetch only special-cased 204-as-empty, so
// `.json()` on that empty-but-200-range body threw "Unexpected end of JSON input" --
// surfaced as every job's final artifact write silently corrupting its own error
// message. PostgREST accepts multiple comma-separated Prefer directives in one header.
async function upsertArtifact(accessToken, runId, kind, content) {
  await pgFetch(accessToken, "artifacts?on_conflict=run_id,kind", {
    method: "POST",
    headers: { Prefer: "resolution=merge-duplicates,return=representation" },
    body: JSON.stringify({ run_id: runId, kind, content: content ?? {} }),
  });
}

// ============================================================================
// Durable Object -- one instance per analysis job. alarm() drives the state machine;
// Cloudflare guarantees alarm delivery across DO eviction/restart, which is the actual
// mechanism that lets a job keep advancing while the browser that submitted it is fully
// closed (see wrangler.toml's header comment).
// ============================================================================
export class P01AnalysisJob extends DurableObject {
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/init" && request.method === "POST") {
      return this.handleInit(await request.json());
    }
    if (url.pathname === "/status" && request.method === "GET") {
      return this.handleStatus();
    }
    if (url.pathname === "/cancel" && request.method === "POST") {
      return this.handleCancel();
    }
    return new Response("not found", { status: 404 });
  }

  async handleInit(payload) {
    const { runId, accessToken, apiKey, proxyUrl, model, courseLabel, chunks } = payload;
    // one Promise.all(...) wave per round, exactly p01-runner.js:561-601's shape --
    // result/err/retryable per chunk, aggregated across rounds.
    const chunkState = chunks.map((chunk) => ({ chunk, result: null, err: null, retryable: false }));
    await this.ctx.storage.put({
      runId, accessToken, apiKey, proxyUrl, model, courseLabel,
      chunkState, round: 1, status: "queued", cancelled: false,
    });
    await this.ctx.storage.setAlarm(Date.now());
    return new Response(JSON.stringify({ jobId: runId, status: "queued" }), { status: 202, headers: { "content-type": "application/json" } });
  }

  async handleStatus() {
    const [status, chunkState, round, error] = await Promise.all([
      this.ctx.storage.get("status"), this.ctx.storage.get("chunkState"), this.ctx.storage.get("round"), this.ctx.storage.get("error"),
    ]);
    if (status === undefined) return new Response(JSON.stringify({ status: "unknown" }), { status: 404, headers: { "content-type": "application/json" } });
    const total = (chunkState || []).length;
    const done = (chunkState || []).filter((s) => s.result).length;
    const failed = (chunkState || []).filter((s) => s.err && !s.retryable).length;
    return new Response(JSON.stringify({ status, round, chunksTotal: total, chunksDone: done, chunksFailed: failed, error: error || null }), {
      headers: { "content-type": "application/json" },
    });
  }

  async handleCancel() {
    const status = await this.ctx.storage.get("status");
    if (status === "done" || status === "error" || status === "cancelled") {
      return new Response(JSON.stringify({ status }), { headers: { "content-type": "application/json" } });
    }
    await this.ctx.storage.put("cancelled", true);
    return new Response(JSON.stringify({ status: "cancelling" }), { headers: { "content-type": "application/json" } });
  }

  async alarm() {
    const cancelled = await this.ctx.storage.get("cancelled");
    const accessToken = await this.ctx.storage.get("accessToken");
    const runId = await this.ctx.storage.get("runId");

    if (cancelled) {
      await this.ctx.storage.put("status", "cancelled");
      await patchRun(accessToken, runId, { status: "cancelled", finished_at: new Date().toISOString() }).catch(() => {});
      return;
    }

    const status = await this.ctx.storage.get("status");
    if (status === "queued") {
      await this.ctx.storage.put("status", "running");
      await patchRun(accessToken, runId, { status: "running" }).catch(() => {});
    }

    const [apiKey, proxyUrl, model, courseLabel, round] = await Promise.all([
      this.ctx.storage.get("apiKey"), this.ctx.storage.get("proxyUrl"), this.ctx.storage.get("model"),
      this.ctx.storage.get("courseLabel"), this.ctx.storage.get("round"),
    ]);
    let chunkState = await this.ctx.storage.get("chunkState");

    // ---- p01-runner.js:562-601, one round per alarm() invocation ----
    const targets = chunkState.filter((s) => !s.result && (round === 1 || s.retryable));
    if (targets.length) {
      for (let i = 0; i < targets.length; i += CHUNK_CONCURRENCY) {
        const wave = targets.slice(i, i + CHUNK_CONCURRENCY);
        await Promise.all(
          wave.map(async (state) => {
            const chunk = state.chunk;
            try {
              const result = await callChunkAnalysis({ proxyUrl, apiKey, model, courseLabel, chunk, maxAttempts: 1 });
              result.chunk_range = result.chunk_range || chunk.range;
              state.result = result;
              state.err = null;
            } catch (err) {
              state.err = err;
              state.retryable = !!err.retryable;
            }
          })
        );
      }
      await this.ctx.storage.put("chunkState", chunkState);
      // partial progress visible mid-run through the existing (unmodified) rowToEntry()
      const partialUnitMap = normalizeUnitMap(makeUnitMap(chunkState.filter((s) => s.result).map((s) => s.result)));
      await upsertArtifact(accessToken, runId, "unit_map", partialUnitMap).catch(() => {});
    }

    const stillRetryable = chunkState.filter((s) => !s.result && s.retryable);
    if (stillRetryable.length && round < MAX_RETRY_ROUNDS) {
      await this.ctx.storage.put("round", round + 1);
      await this.ctx.storage.setAlarm(Date.now() + ROUND_RETRY_DELAY_MS); // p01-runner.js:598
      return;
    }

    // ---- rounds exhausted (or nothing left retryable) -- aggregate and finish, mirrors
    // p01-runner.js:603-613 (skipRefine/skipQuestionGen both true for this caller, so
    // nothing between chunk-analysis and buildGraph ever runs) ----
    try {
      const chunkResults = chunkState.map((s) => s.result || { chunk_range: s.chunk.range, units: [], concepts: [], error: String(s.err ? s.err.message : "알 수 없는 오류") });
      const failedChunks = chunkResults.filter((c) => c.error);
      const unitMap = normalizeUnitMap(makeUnitMap(chunkResults.filter((c) => !c.error)));
      let graph = null;
      let graphGenerated = false;
      try {
        graph = buildGraph(unitMap, []);
        graphGenerated = true;
      } catch (e) {
        /* graph_generated:false, matches p01-runner.js:804-809's own catch-and-continue */
      }
      const questions = { questions: [] }; // skipQuestionGen:true for this caller, always

      await Promise.all([
        upsertArtifact(accessToken, runId, "unit_map", unitMap),
        upsertArtifact(accessToken, runId, "questions", questions),
        upsertArtifact(accessToken, runId, "graph", graphGenerated ? graph : { error: "graph_generated=false" }),
        upsertArtifact(accessToken, runId, "refine_fixes", []),
      ]);
      await patchRun(accessToken, runId, {
        status: "done",
        finished_at: new Date().toISOString(),
        input_meta: {
          extractor: "pdfjs", chunk_count: chunkState.length, failed_chunk_count: failedChunks.length,
          unit_count: Object.keys(unitMap).length, graph_generated: graphGenerated,
          refine_fixes_applied: 0, refine_fixes_rejected: 0,
        },
      });
      await this.ctx.storage.put("status", "done");
    } catch (err) {
      await this.ctx.storage.put({ status: "error", error: String(err.message || err) });
      await patchRun(accessToken, runId, { status: "error", error: String(err.message || err), finished_at: new Date().toISOString() }).catch(() => {});
    }
  }
}

// ============================================================================
// Top-level Worker -- routes requests, creates/looks up the Durable Object per job.
// ============================================================================
function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : "";
  return allowed
    ? { "access-control-allow-origin": allowed, "access-control-allow-methods": "GET, POST, OPTIONS", "access-control-allow-headers": "content-type, authorization" }
    : {};
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("origin") || "";
    const url = new URL(request.url);
    const headers = corsHeaders(origin);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });

    if (url.pathname === "/analyses" && request.method === "POST") {
      const body = await request.json();
      const { model, courseLabel, chunks, nvidiaApiKey, proxyUrl, supabaseAccessToken, sourceFilename } = body;
      if (!nvidiaApiKey || !proxyUrl || !supabaseAccessToken || !Array.isArray(chunks) || !chunks.length) {
        return new Response(JSON.stringify({ error: "INVALID_REQUEST", message: "model/courseLabel/chunks/nvidiaApiKey/proxyUrl/supabaseAccessToken 필요" }), {
          status: 422, headers: { ...headers, "content-type": "application/json" },
        });
      }
      let run;
      try {
        run = await insertQueuedRun(supabaseAccessToken, { model, input_meta: { source_filename: sourceFilename || null, extractor: "pdfjs" } });
      } catch (err) {
        return new Response(JSON.stringify({ error: "UNAUTHORIZED", message: err.message }), { status: 401, headers: { ...headers, "content-type": "application/json" } });
      }
      const stub = env.P01_JOBS.get(env.P01_JOBS.idFromName(run.id));
      const initRes = await stub.fetch("http://do/init", {
        method: "POST",
        body: JSON.stringify({ runId: run.id, accessToken: supabaseAccessToken, apiKey: nvidiaApiKey, proxyUrl, model, courseLabel: courseLabel || "Java", chunks }),
      });
      const initBody = await initRes.text();
      return new Response(initBody, { status: initRes.status, headers: { ...headers, "content-type": "application/json" } });
    }

    // D-fix (security review, found live post-verification): neither route below used
    // to check WHO was asking -- a job UUID alone was enough to view or cancel anyone's
    // analysis (an IDOR: "pdf_analysis runs read all"/"update own" RLS exists precisely
    // to gate this at the DB layer, but these routes talked to the Durable Object
    // directly, bypassing it entirely). Both now require the caller's own Supabase
    // access token and prove authorization by reusing those SAME RLS policies via a
    // real PostgREST call, instead of reimplementing ownership logic here.
    const analysisMatch = url.pathname.match(/^\/analyses\/([^/]+)(\/cancel)?$/);
    if (analysisMatch && analysisMatch[2] && request.method === "POST") {
      const jobId = analysisMatch[1];
      const body = await request.json().catch(() => ({}));
      const callerToken = body.supabaseAccessToken;
      if (!callerToken) {
        return new Response(JSON.stringify({ error: "UNAUTHORIZED", message: "supabaseAccessToken 필요" }), { status: 401, headers: { ...headers, "content-type": "application/json" } });
      }
      // Ownership proof IS this call: "runs update own" RLS only lets it affect a row
      // where member_id = auth.uid(). Scoped to still-in-progress rows too, so cancelling
      // an already-finished run can't retroactively relabel its real outcome; a 0-row
      // result covers "not yours" and "already finished" alike without distinguishing
      // them in the response (avoids leaking which one it was).
      let affected;
      try {
        affected = await patchRun(callerToken, jobId, { status: "cancelled", finished_at: new Date().toISOString() }, "&status=in.(queued,running)");
      } catch (err) {
        return new Response(JSON.stringify({ error: "FORBIDDEN", message: "본인이 제출한, 아직 진행 중인 분석만 취소할 수 있습니다" }), { status: 403, headers: { ...headers, "content-type": "application/json" } });
      }
      if (!affected || !affected.length) {
        return new Response(JSON.stringify({ error: "FORBIDDEN", message: "본인이 제출한, 아직 진행 중인 분석만 취소할 수 있습니다" }), { status: 403, headers: { ...headers, "content-type": "application/json" } });
      }
      const stub = env.P01_JOBS.get(env.P01_JOBS.idFromName(jobId));
      const doRes = await stub.fetch("http://do/cancel", { method: "POST" });
      const doBody = await doRes.text();
      return new Response(doBody, { status: doRes.status, headers: { ...headers, "content-type": "application/json" } });
    }
    if (analysisMatch && !analysisMatch[2] && request.method === "GET") {
      const jobId = analysisMatch[1];
      // D-fix (security review): was url.searchParams.get("token") -- a bearer credential
      // in a URL query string ends up in Cloudflare's own request logs (this account's
      // observability tooling included) and any intermediary's access logs, same class of
      // issue as CWE-598. Authorization header isn't logged that way and isn't cached/
      // replayable off a copied URL; corsHeaders() above already allows it on preflight.
      const authHeader = request.headers.get("authorization") || "";
      const callerToken = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : "";
      if (!callerToken) {
        return new Response(JSON.stringify({ error: "UNAUTHORIZED", message: "Authorization: Bearer <token> 헤더 필요" }), { status: 401, headers: { ...headers, "content-type": "application/json" } });
      }
      try {
        await verifyReadAccess(callerToken, jobId);
      } catch (err) {
        return new Response(JSON.stringify({ error: "UNAUTHORIZED", message: "유효한 로그인 세션이 필요합니다" }), { status: 401, headers: { ...headers, "content-type": "application/json" } });
      }
      const stub = env.P01_JOBS.get(env.P01_JOBS.idFromName(jobId));
      const doRes = await stub.fetch("http://do/status", { method: "GET" });
      const doBody = await doRes.text();
      return new Response(doBody, { status: doRes.status, headers: { ...headers, "content-type": "application/json" } });
    }

    return new Response(JSON.stringify({ error: "NOT_FOUND" }), { status: 404, headers: { ...headers, "content-type": "application/json" } });
  },
};
