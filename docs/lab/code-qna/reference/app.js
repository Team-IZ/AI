// Loads prompt_manifest.json once, renders the generic stage-card UI (shared across
// P01/P02/P03), and tracks per-stage overrides in memory. Pipeline-specific execution
// logic lives in p01-runner.js / p02-runner.js / p03-runner.js, which read the current
// (possibly-edited) stage state via LabApp.getOverride()/getStageDefault().
const LabApp = (() => {
  let manifest = null;
  const overrides = { p01: {}, p02: {}, p03: {} };
  let activePipeline = "p02";
  const runners = {}; // pipeline -> { renderInput(container), run() }
  const timers = {}; // pipeline -> { startMs, intervalId }
  const renderedPipelines = new Set(); // D-K: which pipelines' DOM has already been built once

  // D182: moved here from p01-runner.js (was a private const there) -- P03's model
  // selection was buried inside a stage-card param field instead of getting the same
  // top-level toggle P01 has, and this list's own notes already reference BOTH pipelines'
  // benchmark results ("P03 종합 2위" etc.), so a single shared source was already the
  // right shape, just not extracted yet. Two independent copies could silently drift.
  //
  // Ranking + notes sourced from docs/pipelines.html's 11-model 4-axis table (D116). That
  // benchmark measures a DIFFERENT task (P03 question-gen x grading) -- P01-T1 (D119/D120)
  // separately found step-3.5-flash (rank #1 there) fails completely on P01's chunk-analysis
  // task (0/50) while qwen3-next-80b succeeds 96%. So `tier` below is P01-specific evidence
  // (good/bad), not a re-skin of the P03 rank -- the other 9 are honestly labeled unverified
  // for P01 rather than implying the P03 ranking transfers.
  //
  // D183 (2026-07-15): D-G's "0/50 might be a reasoning_content bug" suspicion (2026-07-14)
  // is now CONFIRMED, not just theorized -- a real 524 on a P03 interview (D181's 4000-char
  // duplicate-definition code context) prompted a from-scratch investigation. Direct curl to
  // NVIDIA, same prompt, model-only swapped: qwen3-next-80b took 75.9-95.9s per call (3/3
  // eventually succeeded, but this project's own history shows this model intermittently
  // 524s exactly in this range -- D142/D144/D145); step-3.5-flash took 1.5-3.9s for the
  // identical prompt via chatTool's real tool_choice path (tool_calls populated correctly
  // every time, questions were concretely grounded in the actual code). Separately verified
  // step-3.5-flash's chatJSON path too (P01's actual mechanism, not just P03's): a
  // realistic 10-page chunk-analysis prompt came back in 4.1-5.3s, answer in
  // reasoning_content with content:null every time -- exactly D-G's hypothesis, and
  // llm.js's existing D131 fallback recovered it cleanly in all 3 calls. D120's "0/50" was
  // measuring the OLD pipeline's missing fallback, not a real model failure. Promoted to
  // shared.default_model for both P01 and P03 on this evidence, not speculation.
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

  // D182: shared model-toggle chip renderer -- P01 had this exact logic (chips, active
  // highlight, note-on-select) as a private function; P03 needs identical behavior. Doesn't
  // own selection state itself (getSelected/setSelected are the caller's own variable) so
  // P01 and P03 picking different models never cross-contaminate each other.
  function renderModelToggle(container, groupSelector, noteSelector, getSelected, setSelected) {
    const group = container.querySelector(groupSelector);
    const note = container.querySelector(noteSelector);
    group.innerHTML = MODEL_CHOICES.map((m) => {
      const cls = ["model-chip"];
      if (m.id === getSelected()) cls.push("active");
      if (m.tier === "bad") cls.push("warn");
      return `<button type="button" class="${cls.join(" ")}" data-model="${escapeHtml(m.id)}">${escapeHtml(m.label)}</button>`;
    }).join("");
    const updateNote = () => {
      const m = MODEL_CHOICES.find((x) => x.id === getSelected());
      note.textContent = m ? m.note : "";
      note.className = "model-note" + (m && m.tier === "bad" ? " warn" : "");
    };
    group.querySelectorAll(".model-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        setSelected(btn.dataset.model);
        group.querySelectorAll(".model-chip").forEach((b) => b.classList.toggle("active", b === btn));
        updateNote();
      });
    });
    updateNote();
  }

  function formatElapsed(ms) {
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  // Live stopwatch next to the Run button -- started right before the actual work begins
  // (not on the click itself, so an early-return validation error doesn't leave it running)
  // and always stopped exactly once, on whichever path the run() ends on.
  function startTimer(pipelineId) {
    stopTimer(pipelineId); // clear any stale interval from a previous run
    const el = document.getElementById(`run-timer-${pipelineId}`);
    // D-K: disabling the run button here (not a separate isRunning flag) piggybacks on
    // the same start/stop pairing every runner already calls exactly once per run on
    // every exit path (see README D146) -- one less piece of state that could drift out
    // of sync with what's actually on screen. Blocks the double-click/re-click-after-
    // switching-tabs-back case that could otherwise start a second concurrent run.
    const btn = document.getElementById(`run-btn-${pipelineId}`);
    if (btn) { btn.disabled = true; btn.textContent = "실행 중..."; }
    const startMs = Date.now();
    if (el) el.textContent = "00:00";
    const intervalId = setInterval(() => {
      if (el) el.textContent = formatElapsed(Date.now() - startMs);
    }, 1000);
    timers[pipelineId] = { startMs, intervalId };
    return startMs;
  }

  function stopTimer(pipelineId) {
    const btn = document.getElementById(`run-btn-${pipelineId}`);
    if (btn) { btn.disabled = false; btn.textContent = "실행"; }
    const t = timers[pipelineId];
    if (!t) return 0;
    clearInterval(t.intervalId);
    delete timers[pipelineId];
    const elapsedMs = Date.now() - t.startMs;
    const el = document.getElementById(`run-timer-${pipelineId}`);
    if (el) el.textContent = formatElapsed(elapsedMs);
    return elapsedMs;
  }

  async function loadManifest() {
    const res = await fetch("prompt_manifest.json");
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

  function clearOverride(pipelineId, stageId) {
    delete overrides[pipelineId][stageId];
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

  function registerRunner(pipelineId, runner) { runners[pipelineId] = runner; }

  function renderPlaceholderChips(list, optional) {
    return (list || [])
      .map((p) => `<span class="placeholder-chip${optional ? " optional" : ""}">{${escapeHtml(p)}}</span>`)
      .join("");
  }

  function fillTemplate(template, values) {
    return template.replace(/\{(\w+)\}/g, (m, key) => (key in values ? String(values[key]) : m));
  }

  function stageCardHtml(pipelineId, stage) {
    const isPrompt = stage.kind === "prompt";
    const kindLabel = isPrompt ? "PROMPT" : "PARAMS";
    const safeId = escapeHtml(stage.id);
    const lastSegment = stage.id.split("-").pop();
    const badgeText = /^\d+$/.test(lastSegment) ? lastSegment : "+";
    return `
      <div class="stage-card" data-stage-id="${safeId}">
        <div class="stage-head" data-toggle="${safeId}">
          <div class="stage-head-left">
            <span class="stage-num">${escapeHtml(badgeText)}</span>
            <span class="stage-title">${escapeHtml(stage.title)}</span>
            <span class="stage-fn">${escapeHtml(stage.function || "")}</span>
          </div>
          <span class="stage-kind-badge ${isPrompt ? "prompt" : ""}">${kindLabel}</span>
        </div>
        <div class="stage-body" id="body-${pipelineId}-${safeId}"></div>
      </div>`;
  }

  function renderPromptStageBody(pipelineId, stage, container) {
    const hasSystem = "system" in stage;
    const hasUser = "user_template" in stage;
    const hasLevel = "level_template" in stage;
    let html = "";
    if (stage.note) html += `<p class="param-field" style="color:var(--ink-faint); margin-bottom:8px;">${escapeHtml(stage.note)}</p>`;
    if (hasSystem) {
      html += `<div class="field-label">system prompt</div>
        <textarea data-field="system" data-pipeline="${pipelineId}" data-stage="${stage.id}">${escapeHtml(resolveTemplate(pipelineId, stage.id, "system"))}</textarea>`;
    }
    if (hasSystem === false && hasLevel) {
      html += `<div class="field-label">공유 헤더 (모든 레벨 공통)</div>
        <textarea data-field="shared_header" data-pipeline="${pipelineId}" data-stage="${stage.id}" readonly style="opacity:0.75;">${escapeHtml(stage.shared_header || "(이전 단계 참고)")}</textarea>`;
    }
    const templateKey = hasUser ? "user_template" : hasLevel ? "level_template" : null;
    if (templateKey) {
      html += `<div class="field-label">${hasUser ? "user template" : "level template"}</div>
        <textarea data-field="${templateKey}" data-pipeline="${pipelineId}" data-stage="${stage.id}">${escapeHtml(resolveTemplate(pipelineId, stage.id, templateKey))}</textarea>`;
    }
    if (stage.required_placeholders || stage.optional_placeholders) {
      html += `<div class="field-label">placeholder</div><div>`;
      html += renderPlaceholderChips(stage.required_placeholders, false);
      html += renderPlaceholderChips(stage.optional_placeholders, true);
      html += `</div>`;
    }
    if (stage.rubric) {
      html += `<div class="field-label">rubric (편집 시 이 실행 기록에 rubric_overridden=true로 표시됨)</div>`;
      for (const [axis, levels] of Object.entries(stage.rubric)) {
        const safeAxis = escapeHtml(axis);
        html += `<div class="rubric-axis"><h4>${safeAxis}</h4>`;
        for (const score of ["5", "4", "3", "2", "1"]) {
          html += `<div class="rubric-level"><span class="score">${score}점</span>
            <textarea data-field="rubric" data-axis="${safeAxis}" data-score="${score}" data-pipeline="${pipelineId}" data-stage="${escapeHtml(stage.id)}">${escapeHtml(levels[score])}</textarea></div>`;
        }
        html += `</div>`;
      }
    }
    if (stage.params && stage.params.length) {
      html += renderParamGrid(pipelineId, stage);
    }
    html += `<div class="stage-actions">
        <button class="secondary" data-action="reset" data-pipeline="${pipelineId}" data-stage="${stage.id}">기본값으로 초기화</button>
        <button class="secondary" data-action="preview" data-pipeline="${pipelineId}" data-stage="${stage.id}">해석된 프롬프트 미리보기</button>
      </div>
      <pre class="preview-pane hidden" id="preview-${pipelineId}-${stage.id}"></pre>`;
    if (stage.advanced) html = `<p style="color:var(--ink-faint); font-size:0.74rem; margin-bottom:10px;">고급 — 3단계 전부가 공유하는 JSON 복구용 프롬프트, 셋 중 하나가 유효한 JSON을 못 돌려줄 때만 자동 호출됨.</p>` + html;
    container.innerHTML = html;
    wireStageInputs(pipelineId, stage, container);
  }

  // D157 (2026-07-15): locked params (e.g. P01/P03's temperature=0, "고정 — 재현성
  // 요구사항") used to still render as a disabled input with a "고정" tag -- user
  // pointed out there's no reason to show a value nobody can ever change. Skipped
  // entirely now instead of shown-but-disabled; the fixed value and its reason still
  // live in the manifest's `note` and this file's own decision log, just not on screen.
  function renderParamGrid(pipelineId, stage) {
    // If every param on this stage is locked (e.g. P03's p03-6, which only ever had
    // max_turns), there's nothing left to show -- skip the "파라미터" heading too rather
    // than leaving an empty grid under a label.
    if (!stage.params.some((p) => !p.locked)) return "";
    let html = `<div class="field-label">파라미터</div><div class="param-grid">`;
    const safeStageId = escapeHtml(stage.id);
    for (const p of stage.params) {
      if (p.locked) continue; // D157: locked params aren't shown at all, not just disabled
      const val = resolveParam(pipelineId, stage.id, p.key);
      const safeKey = escapeHtml(p.key);
      if (p.type === "string_list") {
        html += `<label class="param-field"><span>${safeKey}</span>
          <input type="text" data-field="param" data-key="${safeKey}" data-pipeline="${pipelineId}" data-stage="${safeStageId}" value="${escapeHtml((val || []).join(", "))}"></label>`;
      } else if (p.type === "regex_readonly") {
        html += `<label class="param-field"><span>${safeKey} <span class="locked-tag">읽기전용(v1)</span></span>
          <input type="text" value="${escapeHtml(val)}" disabled></label>`;
      } else if (p.type === "string_secret") {
        html += `<label class="param-field"><span>${safeKey}</span>
          <input type="password" data-field="param" data-key="${safeKey}" data-pipeline="${pipelineId}" data-stage="${safeStageId}" value=""></label>`;
      } else {
        html += `<label class="param-field"><span>${safeKey}</span>
          <input type="text" data-field="param" data-key="${safeKey}" data-pipeline="${pipelineId}" data-stage="${safeStageId}" value="${escapeHtml(val === null || val === undefined ? "" : String(val))}"></label>`;
      }
    }
    html += `</div>`;
    return html;
  }

  function renderParamsStageBody(pipelineId, stage, container) {
    let html = "";
    if (stage.note) html += `<p style="color:var(--ink-faint); font-size:0.78rem; margin-bottom:10px;">${escapeHtml(stage.note)}</p>`;
    html += renderParamGrid(pipelineId, stage);
    html += `<div class="stage-actions"><button class="secondary" data-action="reset" data-pipeline="${pipelineId}" data-stage="${stage.id}">기본값으로 초기화</button></div>`;
    container.innerHTML = html;
    wireStageInputs(pipelineId, stage, container);
  }

  function wireStageInputs(pipelineId, stage, container) {
    container.querySelectorAll("textarea[data-field]").forEach((el) => {
      if (el.readOnly) return;
      el.addEventListener("input", () => {
        el.classList.add("dirty");
        const field = el.dataset.field;
        if (field === "rubric") {
          const axis = el.dataset.axis, score = el.dataset.score;
          const currentRubric = getOverride(pipelineId, stage.id)?.rubric || JSON.parse(JSON.stringify(stage.rubric));
          currentRubric[axis][score] = el.value;
          setOverride(pipelineId, stage.id, { rubric: currentRubric, rubric_overridden: true });
        } else {
          setOverride(pipelineId, stage.id, { [field]: el.value });
        }
      });
    });
    container.querySelectorAll("input[data-field='param']").forEach((el) => {
      el.addEventListener("input", () => {
        const ov = getOverride(pipelineId, stage.id) || {};
        const params = { ...(ov.params || {}) };
        const paramDef = stage.params.find((p) => p.key === el.dataset.key);
        let v = el.value;
        if (paramDef && paramDef.type === "int") v = parseInt(v, 10);
        if (paramDef && paramDef.type === "float") v = parseFloat(v);
        if (paramDef && paramDef.type === "string_list") v = v.split(",").map((s) => s.trim()).filter(Boolean);
        params[el.dataset.key] = v;
        setOverride(pipelineId, stage.id, { params });
      });
    });
    container.querySelectorAll("button[data-action='reset']").forEach((btn) => {
      btn.addEventListener("click", () => {
        clearOverride(pipelineId, stage.id);
        renderStageBody(pipelineId, stage, container);
      });
    });
    container.querySelectorAll("button[data-action='preview']").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pre = document.getElementById(`preview-${pipelineId}-${stage.id}`);
        const tmpl = resolveTemplate(pipelineId, stage.id, stage.user_template ? "user_template" : "level_template") || "";
        const sample = {};
        (stage.required_placeholders || []).forEach((p) => { sample[p] = `<${p} 실제값>`; });
        (stage.optional_placeholders || []).forEach((p) => { sample[p] = `<${p}>`; });
        pre.textContent = fillTemplate(tmpl, sample);
        pre.classList.remove("hidden");
      });
    });
  }

  function renderStageBody(pipelineId, stage, container) {
    if (stage.kind === "prompt") renderPromptStageBody(pipelineId, stage, container);
    else renderParamsStageBody(pipelineId, stage, container);
  }

  // Escapes for BOTH text-node and attribute-value contexts -- every innerHTML
  // interpolation site in this file (including manifest-derived strings, not just
  // user-typed ones) routes through this, on the assumption that the manifest itself
  // may become teammate-editable/shared later (Supabase `presets`, PLAN.md section 4).
  // Un-escaped quotes previously let a value break out of value="..." and inject a new
  // attribute (e.g. onmouseover=...) -- fixed by also escaping " and '.
  function escapeHtml(v) {
    if (v === undefined || v === null) return "";
    return String(v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // D-K (2026-07-15, README D146): each pipeline now gets ONE persistent container,
  // built once and then only shown/hidden on tab switches -- never rebuilt. This used to
  // replace #pipeline-view's entire innerHTML on every tab click, which destroyed the
  // OTHER pipeline's in-progress run state (progress log, run status, timer, stage-card
  // open/closed state) the instant you switched away, with no way to get it back even by
  // switching back. Confirmed live (headless-browser repro) that the run() call itself
  // kept executing in the background regardless and still reached showResults() correctly
  // (that panel lives outside #pipeline-view) -- only the VISIBILITY was ever broken, but
  // that alone made an already-finished/still-running job look identical to "never
  // started" once you returned, with the run button re-enabled and no indicator either
  // way (see D-K's startTimer/stopTimer change for the other half of that fix).
  function renderPipeline(pipelineId) {
    activePipeline = pipelineId;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.pipeline === pipelineId));
    const view = document.getElementById("pipeline-view");

    view.querySelectorAll(".pipeline-container").forEach((el) => {
      el.classList.toggle("hidden", el.dataset.pipeline !== pipelineId);
    });
    if (renderedPipelines.has(pipelineId)) return; // already built above -- just shown, not rebuilt
    renderedPipelines.add(pipelineId);

    const container = document.createElement("div");
    container.className = "pipeline-container";
    container.dataset.pipeline = pipelineId;

    const p = manifest.pipelines[pipelineId];
    let html = "";
    if (!p.has_llm_calls) {
      html += `<div class="pipeline-banner"><b>LLM 미호출</b> — ${escapeHtml(p.banner || "이 파이프라인은 프롬프트가 아니라 파라미터를 편집합니다.")}</div>`;
    }
    html += `<div id="input-panel-${pipelineId}"></div>`;
    html += `<div class="stage-list">`;
    for (const stage of p.stages) html += stageCardHtml(pipelineId, stage);
    html += `</div>`;
    html += `<div class="run-bar">
        <button class="primary" id="run-btn-${pipelineId}">실행</button>
        <span class="run-timer" id="run-timer-${pipelineId}"></span>
        <span class="run-status" id="run-status-${pipelineId}"></span>
      </div>
      <div class="progress-log hidden" id="progress-log-${pipelineId}"></div>`;
    container.innerHTML = html;
    view.appendChild(container);

    container.querySelectorAll("[data-toggle]").forEach((head) => {
      head.addEventListener("click", () => {
        const card = head.closest(".stage-card");
        const stageId = head.dataset.toggle;
        const wasOpen = card.classList.contains("open");
        card.classList.toggle("open", !wasOpen);
        if (!wasOpen) {
          const body = document.getElementById(`body-${pipelineId}-${stageId}`);
          if (!body.dataset.rendered) {
            const stage = getStage(pipelineId, stageId);
            renderStageBody(pipelineId, stage, body);
            body.dataset.rendered = "1";
          }
        }
      });
    });

    const runner = runners[pipelineId];
    if (runner) {
      runner.renderInput(document.getElementById(`input-panel-${pipelineId}`));
      document.getElementById(`run-btn-${pipelineId}`).addEventListener("click", () => runner.run());
    }
  }

  function log(pipelineId, msg) {
    const el = document.getElementById(`progress-log-${pipelineId}`);
    if (!el) return;
    el.classList.remove("hidden");
    const line = document.createElement("div");
    line.textContent = `[${new Date().toISOString().slice(11, 19)}] ${msg}`;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  function setStatus(pipelineId, text, kind) {
    const el = document.getElementById(`run-status-${pipelineId}`);
    if (!el) return;
    el.textContent = text;
    el.className = "run-status" + (kind ? ` ${kind}` : "");
  }

  function showResults(html) {
    const view = document.getElementById("results-view");
    document.getElementById("results-content").innerHTML = html;
    view.classList.remove("hidden");
    view.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // D175 (2026-07-15): a Codex-assisted DB/code audit (requested by the user) found that a
  // run whose outer try/catch fires (a hard failure before reaching its own success-shaped
  // maybeSaveRun()) leaves NO trace in the DB at all -- not even a status='error' row.
  // Every pipeline's outer catch already has pipelineId/model/startedAt/err in scope, so
  // this is a single shared helper (not tripled across p01/p02/p03-runner.js) called from
  // each. Mirrors maybeSaveRun()'s own existing "not configured / not logged in -> just
  // log, never throw" tolerance -- a failed attempt to record a failure must never itself
  // crash the run() catch block that's already handling a real error.
  async function saveFailedRun(pipelineId, model, err, startedAt) {
    if (!LabDB.isConfigured()) return;
    try {
      await LabDB.saveRun({
        pipeline: pipelineId, model: model || null, input_meta: {}, overrides: {}, rubric_overridden: false,
        artifacts: [], status: "error", error: String((err && err.message) || err),
        started_at: startedAt.toISOString(), finished_at: new Date().toISOString(),
      });
      log(pipelineId, "실패한 실행이 DB에 기록됨");
    } catch (saveErr) {
      log(pipelineId, `실패 기록 DB 저장도 실패: ${saveErr.message}`);
    }
  }

  // D162 (2026-07-15): all 3 runners' "원본 JSON" block used to hardcode
  // `.slice(0, 20000)` with no indication anything was cut -- a real 9-unit/26-chunk P01
  // result silently lost content mid-structure, and the CSS has no height clipping
  // (.results-view pre only sets overflow-x), so the user correctly read it as the raw
  // JSON itself being cut, not a viewport issue. Same exact pattern existed identically in
  // all 3 runners (grep-checked), so fixed once here instead of three separate patches.
  // INLINE_CAP (500,000 chars) is a generous safety backstop against a truly pathological
  // result, not a normal ceiling -- every real run seen so far (P01's 9-unit/26-chunk
  // result included) is well under it. A full-JSON download link is offered unconditionally
  // (not just when truncated) so the complete result is never only reachable by raising
  // this number -- this mirrors D153's rule of never leaving output in a silently-partial
  // state.
  const JSON_BLOCK_INLINE_CAP = 500000;
  function jsonResultBlock(title, obj, filename) {
    const full = JSON.stringify(obj, null, 2);
    const shown = full.length > JSON_BLOCK_INLINE_CAP ? full.slice(0, JSON_BLOCK_INLINE_CAP) : full;
    const truncNote = full.length > JSON_BLOCK_INLINE_CAP
      ? `<p style="color:var(--status-blocked); font-size:0.72rem; margin:4px 0;">전체 ${full.length.toLocaleString()}자 중 앞 ${JSON_BLOCK_INLINE_CAP.toLocaleString()}자만 표시됨 -- 전체는 다운로드로 확인.</p>`
      : "";
    const href = `data:application/json;charset=utf-8,${encodeURIComponent(full)}`;
    return `<p class="field-label" style="margin-top:14px;">${escapeHtml(title)}
        <a href="${href}" download="${escapeHtml(filename)}" style="margin-left:10px; font-size:0.72rem; font-weight:normal;">전체 JSON 다운로드</a></p>
      ${truncNote}<pre>${escapeHtml(shown)}</pre>`;
  }

  async function init() {
    await loadManifest();
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => renderPipeline(btn.dataset.pipeline));
    });
    renderPipeline(activePipeline);
  }

  return {
    init, getManifest, getStage, getOverride, setOverride, resolveTemplate, resolveParam,
    registerRunner, fillTemplate, log, setStatus, showResults, escapeHtml,
    startTimer, stopTimer, formatElapsed, jsonResultBlock, saveFailedRun,
    MODEL_CHOICES, renderModelToggle,
  };
})();

document.addEventListener("DOMContentLoaded", LabApp.init);
