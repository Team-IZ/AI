// Pure manifest/template/override layer extracted from Pipeline Lab's app.js
// (popixoxipop-collab/Code_reviewer_with_feedback, docs/lab/app.js). Every function
// body below is copied verbatim from that file except two intentional changes, each
// called out at its call site: (1) loadManifest()'s fetch path, adjusted because these
// pages live one level deeper (trainee/*.html) than the repo root prompt_manifest.json
// sits at; (2) saveFailedRun()'s log(pipelineId, msg) call became an injected onProgress
// callback, since the DOM-backed progress-log panel it used to write to doesn't exist
// here.
//
// Deliberately NOT ported: registerRunner/renderPipeline/the tab registry (no tabs in
// this multi-page structure), log/setStatus/showResults/startTimer/stopTimer (DOM-bound
// to the old single-page layout's element IDs -- p02-engine.js/p03-engine.js replace
// these call sites with hook calls instead), and the entire stage-card prompt/param
// editor UI (renderPromptStageBody/renderParamsStageBody/wireStageInputs/stageCardHtml/
// renderParamGrid/renderPlaceholderChips -- there is no prompt-editing UI in this
// front end). renderModelToggle is also not ported: it's a DOM-rendering helper tied to
// the old stage-card markup's specific selector strings, the same category as the
// deleted renderInput/renderResults -- each new page draws its own model-chip markup
// against the MODEL_CHOICES data below instead.
//
// Global name kept as `LabApp` because db.js's saveRun() calls
// window.LabApp.getManifest() internally to stamp manifest_version on every saved run.
const LabApp = (() => {
  let manifest = null;
  const overrides = { p01: {}, p02: {}, p03: {} };

  async function loadManifest() {
    const res = await fetch("../prompt_manifest.json");
    manifest = await res.json();
    return manifest;
  }

  function getManifest() { return manifest; }

  function getStage(pipelineId, stageId) {
    return manifest.pipelines[pipelineId].stages.find((s) => s.id === stageId);
  }

  function getOverride(pipelineId, stageId) {
    return overrides[pipelineId][stageId] || null;
  }

  function setOverride(pipelineId, stageId, patch) {
    overrides[pipelineId][stageId] = { ...(overrides[pipelineId][stageId] || {}), ...patch };
  }

  // Resolve the actual value a runner should use for a stage's system/user template,
  // applying any edit the user made; falls back to the manifest default.
  function resolveTemplate(pipelineId, stageId, key) {
    const ov = getOverride(pipelineId, stageId);
    if (ov && ov[key] !== undefined) return ov[key];
    const stage = getStage(pipelineId, stageId);
    return stage ? stage[key] : undefined;
  }

  function resolveParam(pipelineId, stageId, key) {
    const ov = getOverride(pipelineId, stageId);
    if (ov && ov.params && ov.params[key] !== undefined) return ov.params[key];
    const stage = getStage(pipelineId, stageId);
    const p = stage && stage.params && stage.params.find((x) => x.key === key);
    return p ? p.default : undefined;
  }

  function fillTemplate(template, values) {
    return template.replace(/\{(\w+)\}/g, (m, key) => (key in values ? String(values[key]) : m));
  }

  // Escapes for BOTH text-node and attribute-value contexts. None of the pure engine
  // functions ported into p02-engine.js/p03-engine.js call this (every original call
  // site lived inside the deleted render functions) -- kept here anyway as the
  // established, tested implementation for the new pages' own render code to reuse,
  // per the XSS discipline the original app relied on (see PLAN.md's textContent
  // invariant, restated in the port plan's "보안 규율 승계" section).
  function escapeHtml(v) {
    if (v === undefined || v === null) return "";
    return String(v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatElapsed(ms) {
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  // D162: generous safety backstop (not a normal ceiling) against a truly pathological
  // result; a full-JSON download link is offered unconditionally so the complete result
  // is never only reachable by raising this number.
  const JSON_BLOCK_INLINE_CAP = 500000;
  function jsonResultBlock(title, obj, filename) {
    const full = JSON.stringify(obj, null, 2);
    const shown = full.length > JSON_BLOCK_INLINE_CAP ? full.slice(0, JSON_BLOCK_INLINE_CAP) : full;
    const truncNote = full.length > JSON_BLOCK_INLINE_CAP
      ? `<p style="font-size:0.72rem; margin:4px 0;">전체 ${full.length.toLocaleString()}자 중 앞 ${JSON_BLOCK_INLINE_CAP.toLocaleString()}자만 표시됨 -- 전체는 다운로드로 확인.</p>`
      : "";
    const href = `data:application/json;charset=utf-8,${encodeURIComponent(full)}`;
    return `<p style="margin-top:14px;">${escapeHtml(title)}
        <a href="${href}" download="${escapeHtml(filename)}" style="margin-left:10px; font-size:0.72rem;">전체 JSON 다운로드</a></p>
      ${truncNote}<pre>${escapeHtml(shown)}</pre>`;
  }

  // D175: a run whose outer try/catch fires (a hard failure before reaching the
  // pipeline's own success-path DB save) used to leave NO trace in the DB at all -- not
  // even a status='error' row. Mirrors saveRun()'s own "not configured / not logged in
  // -> just report, never throw" tolerance -- a failed attempt to record a failure must
  // never itself crash the run() catch block that's already handling a real error.
  // Only change from the original: log(pipelineId, msg) -> injected onProgress(msg).
  async function saveFailedRun(pipelineId, model, err, startedAt, onProgress) {
    if (!LabDB.isConfigured()) return;
    try {
      await LabDB.saveRun({
        pipeline: pipelineId, model: model || null, input_meta: {}, overrides: {}, rubric_overridden: false,
        artifacts: [], status: "error", error: String((err && err.message) || err),
        started_at: startedAt.toISOString(), finished_at: new Date().toISOString(),
      });
      if (onProgress) onProgress("실패한 실행이 DB에 기록됨");
    } catch (saveErr) {
      if (onProgress) onProgress(`실패 기록 DB 저장도 실패: ${saveErr.message}`);
    }
  }

  // D182: model list + notes shared by P01 and P03. Ranking + notes sourced from
  // docs/pipelines.html's 11-model 4-axis table (D116); that benchmark measures a
  // DIFFERENT task (P03 question-gen x grading) -- P01-T1 (D119/D120) separately found
  // step-3.5-flash (rank #1 there) fails completely on P01's chunk-analysis task (0/50)
  // while qwen3-next-80b succeeds 96%. So `tier` below is P01-specific evidence
  // (good/bad), not a re-skin of the P03 rank -- the other 9 are honestly labeled
  // unverified for P01 rather than implying the P03 ranking transfers.
  //
  // D183 (2026-07-15): D-G's "0/50 might be a reasoning_content bug" suspicion
  // (2026-07-14) is now CONFIRMED, not just theorized -- a real 524 on a P03 interview
  // (D181's 4000-char duplicate-definition code context) prompted a from-scratch
  // investigation. Direct curl to NVIDIA, same prompt, model-only swapped:
  // qwen3-next-80b took 75.9-95.9s per call (3/3 eventually succeeded, but this
  // project's own history shows this model intermittently 524s exactly in this range --
  // D142/D144/D145); step-3.5-flash took 1.5-3.9s for the identical prompt via
  // chatTool's real tool_choice path (tool_calls populated correctly every time,
  // questions were concretely grounded in the actual code). Separately verified
  // step-3.5-flash's chatJSON path too (P01's actual mechanism, not just P03's): a
  // realistic 10-page chunk-analysis prompt came back in 4.1-5.3s, answer in
  // reasoning_content with content:null every time -- exactly D-G's hypothesis, and
  // llm.js's existing D131 fallback recovered it cleanly in all 3 calls. D120's "0/50"
  // was measuring the OLD pipeline's missing fallback, not a real model failure.
  // Promoted to shared.default_model for both P01 and P03 on this evidence, not
  // speculation.
  const MODEL_CHOICES = [
    { id: "stepfun-ai/step-3.5-flash", label: "step-3.5-flash", tier: "good",
      note: "기본값(D183) · D120의 '0/50'은 구파이프라인 reasoning_content 버그로 확정(D-G 이론을 실측 확인) · 재검증: P03 tool_calls 1.5-3.9s 3/3, P01 JSON모드 4.1-5.3s 3/3(reasoning_content 경유, 폴백 정상 동작)." },
    { id: "mistralai/mistral-medium-3.5-128b", label: "mistral-medium-3.5", tier: "unverified",
      note: "P01 기준 미검증 · P03 종합 2위(0.749) · D183 부수측정: 동일 4000자 프롬프트 13.2-17.3s(qwen 대비 5-6배 빠름, qwen과의 상대비교로만 측정, 단독 신뢰도 검증은 아직 부족)." },
    { id: "qwen/qwen3-next-80b-a3b-instruct", label: "qwen3-next-80b", tier: "unverified",
      note: "P01-T1 실측 96% 성공(D120, 표본 50). D183: 동일 프롬프트에서 step-3.5-flash 대비 20-50배 느림(75.9-95.9s, 이전 회차엔 3회 중 1회 524도 있었음) -- 더는 기본값 아님, 느림/간헐적 524(D142/D144/D145 기존 이력)로 tier 재평가." },
    { id: "nvidia/nemotron-3-super-120b-a12b", label: "nemotron-3-super-120b", tier: "unverified",
      note: "P01 기준 미검증 · P03에서 범위 밖 점수 출력 결함 이력." },
    { id: "qwen/qwen3.5-122b-a10b", label: "qwen3.5-122b", tier: "unverified",
      note: "P01 기준 미검증 · P03 종합 5위(0.589)." },
    { id: "nvidia/llama-3.3-nemotron-super-49b-v1.5", label: "nemotron-super-49b", tier: "unverified",
      note: "P01 기준 미검증 · P03 종합 6위(0.531)." },
    { id: "deepseek-ai/deepseek-v4-pro", label: "deepseek-v4-pro", tier: "unverified",
      note: "P01 기준 미검증 · P03에서 쿼타 소진 이력(측정 당시 몇 시간 전엔 100%)." },
    { id: "meta/llama-4-maverick-17b-128e-instruct", label: "llama-4-maverick", tier: "unverified",
      note: "P01 기준 미검증 · P03에서 NVIDIA 서빙 장애로 측정 불가 이력." },
    { id: "mistralai/mistral-large-3-675b-instruct-2512", label: "mistral-large-3", tier: "unverified",
      note: "P01 기준 미검증 · P03 채점기 역할에서 퇴행 생성 루프 결함 이력(질문생성 역할만 정상)." },
    { id: "z-ai/glm-5.2", label: "glm-5.2", tier: "unverified",
      note: "P01 기준 미검증 · P03에서 쿼타 소진 이력." },
    { id: "minimaxai/minimax-m3", label: "minimax-m3", tier: "unverified",
      note: "P01 기준 미검증 · P03에서 100회 반복 중 DEGRADED 재발 이력." },
  ];

  return {
    loadManifest, getManifest, getStage, getOverride, setOverride, resolveTemplate, resolveParam,
    fillTemplate, escapeHtml, formatElapsed, jsonResultBlock, saveFailedRun, MODEL_CHOICES,
  };
})();
