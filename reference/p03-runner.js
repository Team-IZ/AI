// P03 v1 is human-in-the-loop only (persona/AI-answerer mode is v2, PLAN.md section 5) --
// a teammate types real answers to real generated questions. Question generation +
// grading are real NVIDIA calls (same manifest prompts as P01/P02). Answer classification
// (surface/partial/defended) is NOT an LLM call in the real pipeline either -- it's the
// same deterministic regex classifiers P02 already proved run fine in Pyodide, so this
// reuses that shared instance rather than re-implementing the classifiers in JS.
const P03Runner = (() => {
  const REPO_RAW_BASE = "https://raw.githubusercontent.com/popixoxipop-collab/Code_reviewer_with_feedback/main/";
  const CLASSIFIER_FILES = [
    "judgment/isolation_classifier.py",
    "judgment/isolation_hook.py",
    "feedback/reflection_signal.py",
    "feedback/reflection_hook.py",
    "judgment/isolation_categories/role_separation/patterns.json",
    "judgment/isolation_categories/domain_irrelevance/patterns.json",
    "judgment/isolation_categories/alt_storage_or_scope/patterns.json",
    "judgment/isolation_categories/perf_optimization/patterns.json",
    "feedback/reflection_patterns/new_judgment/patterns.json",
    "feedback/reflection_patterns/concrete_improvement/patterns.json",
    "feedback/reflection_patterns/reason_explanation/patterns.json",
    "feedback/reflection_patterns/self_error_recognition/patterns.json",
  ];
  const LEVELS = ["l1", "l2", "l3", "reflection"];

  let findings = [];
  let selectedFinding = null;
  let pendingAnswerResolve = null;
  // D182: P03's model choice used to live only inside p03-1/p03-7's collapsed stage-card
  // param fields -- much less discoverable than P01's prominent top-level toggle, and the
  // user asked for parity ("모델 11종 중에 선택할 수 있어야할 거 같은데 교안 분석
  // 파이프라인처럼"). Same sticky-across-tab-switches pattern as P01Runner's own
  // selectedModel (initialized from manifest.shared.default_model on first render).
  let selectedModel = null;
  // D176: real source content for the currently-selected finding, when it came from P02's
  // "인터뷰 시작" connector (see loadFindingFromP02). Empty array for manual JSON-paste
  // findings, which never had a `files` map to draw real code from in the first place.
  // D181: was a single string (one file) -- now an array of {path, content}, since a
  // duplicate-definition finding genuinely spans multiple real files and picking only the
  // first one silently dropped the others (see resolveConnectableFile()/D180 in
  // p02-runner.js). ALL of these feed the LLM prompt (buildCombinedCodeContext); the code
  // panel additionally lets a human tab between them for display only.
  let pendingCodeContexts = [];
  let selectedContextIndex = 0;
  // D176 (2026-07-15) → D184 (2026-07-15): Team-IZ 검증세션 패턴은 채점 결과를 세션 중엔
  // 숨기고 완료 후 별도 클릭으로만 노출했었음 -- 이건 "학생이 자기 점수를 실시간으로 못 보게"
  // 막는 실제 평가 시나리오를 위한 UX였는데, 이 도구의 실제 사용자(팀원들의 파이프라인 자체
  // 테스트)는 그 시나리오가 아니라서 클릭 한 번의 마찰이 그냥 불필요했음(사용자 직접 지적).
  // 라이브 진행 중(턴 사이 appendTranscriptEntry)엔 여전히 verdict를 안 보여주지만(그 부분은
  // 그대로 유효 -- 다음 질문에 영향 안 주려는 의도), 채점이 끝나면 클릭 없이 바로 renderResults().
  let countdownIntervalId = null;
  // D181: unmeasured/provisional -- see resolveMaxAttempts()'s comment for the reasoning.
  const ELEVATED_RATE_THRESHOLD = 30;
  const ELEVATED_MAX_ATTEMPTS = 5;

  function renderInput(container) {
    if (!selectedModel) selectedModel = (LabApp.getManifest().shared || {}).default_model;
    container.innerHTML = `
      <div class="input-panel">
        <h3>인터뷰 대상 finding</h3>
        <p style="color:var(--ink-faint); font-size:0.78rem;">P02 결과 화면의 "인터뷰 시작"을 누르면 자동으로 채워집니다. 직접 붙여넣기도 가능합니다.</p>
        <textarea id="p03-findings-input" placeholder='[{"id": "architecture-diffusion:App.js", "file": "App.js", "finding": "...", "priority": "질문 대상"}]' style="width:100%; min-height:80px; font-family:var(--mono); font-size:0.76rem; padding:8px; border-radius:4px; border:1px solid var(--line-strong); background:var(--bg-panel); color:var(--ink);"></textarea>
        <div class="input-row">
          <button class="secondary" id="p03-load-findings">findings 불러오기</button>
          <select id="p03-finding-select" style="font-family:var(--mono); font-size:0.78rem; padding:7px; border-radius:4px; border:1px solid var(--line-strong); background:var(--bg-panel); color:var(--ink); flex:1; min-width:220px;">
            <option value="">— finding을 먼저 불러오세요 —</option>
          </select>
        </div>
        <div class="field-label" style="margin-top:14px;">모델 선택 (11종 — 질문 생성+채점 전부에 적용)</div>
        <div class="model-toggle-group" id="p03-model-group"></div>
        <p class="model-note" id="p03-model-note"></p>
      </div>
      <div class="p03-session hidden" id="p03-session">
        <div class="p03-session-code">
          <div class="p03-session-code-header">📄 <span id="p03-session-filename">-</span></div>
          <div class="p03-session-code-tabs" id="p03-session-code-tabs"></div>
          <pre class="p03-session-code-body" id="p03-session-code-body">(코드 컨텍스트 없음)</pre>
        </div>
        <div class="p03-session-chat">
          <div class="p03-session-status">
            <span id="p03-progress">질문 -/-</span>
            <span id="p03-countdown"></span>
          </div>
          <div id="p03-transcript"></div>
          <div id="p03-answer-row" class="hidden">
            <textarea id="p03-answer-input" placeholder="답변을 입력하세요..." style="width:100%; min-height:70px; font-family:var(--sans); font-size:0.84rem; padding:10px; border-radius:4px; border:1px solid var(--line-strong); background:var(--bg-panel); color:var(--ink);"></textarea>
            <button class="primary" id="p03-submit-answer" style="margin-top:8px;">답변 제출</button>
          </div>
        </div>
      </div>`;

    container.querySelector("#p03-load-findings").addEventListener("click", () => {
      const raw = container.querySelector("#p03-findings-input").value;
      try {
        findings = JSON.parse(raw);
        if (!Array.isArray(findings)) throw new Error("배열이어야 함");
        selectedFinding = null;
        pendingCodeContexts = []; // D176/D181: 수동 붙여넣기는 실제 파일 내용을 알 길이 없음
        selectedContextIndex = 0;
        renderCodePanel();
        const select = container.querySelector("#p03-finding-select");
        select.innerHTML = findings.map((f, i) => `<option value="${i}">${LabApp.escapeHtml(f.id || `finding ${i}`)}</option>`).join("");
        LabApp.log("p03", `finding ${findings.length}건 로드됨`);
      } catch (err) {
        LabApp.log("p03", `findings JSON 파싱 실패: ${err.message}`);
      }
    });
    container.querySelector("#p03-finding-select").addEventListener("change", (e) => {
      selectedFinding = findings[parseInt(e.target.value, 10)] || null;
      pendingCodeContexts = []; // D176/D181: 드롭다운 재선택 시 이전(P02발) 코드 컨텍스트는 항상 폐기
      selectedContextIndex = 0;
      renderCodePanel();
    });
    LabApp.renderModelToggle(container, "#p03-model-group", "#p03-model-note", () => selectedModel, (v) => { selectedModel = v; });
  }

  // D181: renders the left code panel from pendingCodeContexts/selectedContextIndex. Safe
  // to call any time (findings load, dropdown change, run() start) -- no-ops gracefully if
  // the panel's DOM doesn't exist yet (P03 tab never visited) or there's nothing to show.
  // Purely a DISPLAY concern -- which tab is selected here never changes what actually goes
  // into the LLM prompt (buildCombinedCodeContext always uses ALL contexts, regardless of
  // what the human happens to be looking at at any given moment).
  function renderCodePanel() {
    const header = document.getElementById("p03-session-filename");
    const tabsEl = document.getElementById("p03-session-code-tabs");
    const body = document.getElementById("p03-session-code-body");
    if (!header || !tabsEl || !body) return;
    if (!pendingCodeContexts.length) {
      header.textContent = "(특정 파일 없음)";
      tabsEl.innerHTML = "";
      body.textContent = "(코드 컨텍스트 없음)";
      return;
    }
    if (selectedContextIndex >= pendingCodeContexts.length) selectedContextIndex = 0;
    if (pendingCodeContexts.length > 1) {
      header.textContent = `${pendingCodeContexts.length}개 파일 (전부 질문 생성에 포함됨 -- 탭은 화면 표시용)`;
      tabsEl.innerHTML = pendingCodeContexts
        .map((c, i) => `<button class="p03-code-tab${i === selectedContextIndex ? " active" : ""}" data-tab-idx="${i}">${LabApp.escapeHtml(c.path.split("/").pop())}</button>`)
        .join("");
      tabsEl.querySelectorAll("[data-tab-idx]").forEach((btn) => {
        btn.addEventListener("click", () => {
          selectedContextIndex = parseInt(btn.dataset.tabIdx, 10);
          renderCodePanel();
        });
      });
    } else {
      header.textContent = pendingCodeContexts[0].path;
      tabsEl.innerHTML = "";
    }
    body.textContent = pendingCodeContexts[selectedContextIndex].content || "(코드 컨텍스트 없음)";
  }

  // D181: concatenates every candidate file (labeled with its path) into one prompt-ready
  // string, then applies the manifest's own existing p03-1.truncation.code_context cap to
  // the WHOLE combined text (same simple cap D176 already used for the single-file case --
  // not proportionally splitting per file, a deliberately simple choice: if the first
  // file's content alone exceeds the cap, later files get cut, which is an acceptable
  // provisional behavior, not a silent data-loss regression since D176's single-file case
  // already had this exact same truncation shape).
  function buildCombinedCodeContext(contexts, cap) {
    if (!contexts || !contexts.length) return null;
    const labeled = contexts.map((c) => `--- ${c.path} ---\n${c.content}`).join("\n\n");
    return cap ? labeled.slice(0, cap) : labeled;
  }

  // D181: shared 40rpm ceiling has real prior incidents from P01's chunk bursts (D163-171).
  // P03's own calls are never a burst by themselves (one in-flight call at a time,
  // human-paced between turns) -- the actual risk here is several teammates' P03 sessions
  // overlapping against the same shared proxy/key, invisible to each other without this
  // check. Reuses DebugTraffic's own already-throttled rolling count (no extra server
  // load from calling this before every turn) and, if it's elevated, asks the worker for
  // more retry budget via the SAME x-max-attempts mechanism D169 built for P01, rather than
  // inventing a second retry system. ELEVATED_RATE_THRESHOLD/ELEVATED_MAX_ATTEMPTS (top of
  // file) are both unmeasured/provisional -- 75% of the documented ~40rpm ceiling, and one
  // more than the worker's own MAX_ATTEMPTS=3 default -- flagged as such, not derived from
  // real incident data the way D169's numbers eventually were.
  async function resolveMaxAttempts(pipelineId) {
    if (typeof DebugTraffic === "undefined" || !DebugTraffic.getCurrentRate) return undefined;
    const { count, isServerWide, threshold } = await DebugTraffic.getCurrentRate();
    if (count < ELEVATED_RATE_THRESHOLD) return undefined;
    const scopeNote = isServerWide ? "" : " (이 탭 기준만 -- 다른 팀원 트래픽은 안 잡힘)";
    LabApp.log(pipelineId, `⚠ 현재 트래픽 ${count}/${threshold}${scopeNote} -- 재시도 여유를 ${ELEVATED_MAX_ATTEMPTS}회로 늘려서 요청`);
    return ELEVATED_MAX_ATTEMPTS;
  }

  // D176: entry point called from p02-runner.js's "인터뷰 시작" button. The caller switches
  // to the P03 tab BEFORE calling this (so renderPipeline("p03") has already built the DOM
  // once, synchronously -- see app.js's renderPipeline/D-K), so the direct-DOM writes below
  // are safe. This function itself never runs anything -- it only pre-fills state/DOM.
  // D178: the caller now calls P03Runner.run() immediately after this (user's explicit
  // choice over a manual-confirm step), so in practice a real LLM call fires right after
  // this returns; run()'s own NVIDIA key/proxy guard is what's left protecting an
  // unconfigured teammate from a confusing failure.
  // D181: codeContexts is now an array of {path, content} (was a single string) -- see the
  // module-level pendingCodeContexts comment.
  function loadFindingFromP02(finding, codeContexts) {
    findings = [finding];
    selectedFinding = finding;
    pendingCodeContexts = Array.isArray(codeContexts) ? codeContexts : [];
    selectedContextIndex = 0;
    renderCodePanel();
    const select = document.getElementById("p03-finding-select");
    if (select) {
      select.innerHTML = `<option value="0">${LabApp.escapeHtml(finding.id || "finding 0")}</option>`;
      select.value = "0";
    }
    const textarea = document.getElementById("p03-findings-input");
    if (textarea) textarea.value = JSON.stringify([finding], null, 2);
    const fileNote = pendingCodeContexts.length ? ` (코드 컨텍스트 ${pendingCodeContexts.length}개 파일 포함)` : " (코드 컨텍스트 없음)";
    LabApp.log("p03", `P02에서 finding 전달받음: ${finding.id}${fileNote}`);
  }

  function findingCategory(findingId) {
    // mirrors feedback/evidence_bridge.py::finding_category -- id prefix before the first ':'
    return (findingId || "").split(":")[0];
  }

  async function ensureClassifiers(onProgress) {
    const pyodide = await LabPyodide.get(onProgress);
    if (!LabPyodide.isLoaded("p03")) {
      await LabPyodide.loadFiles(pyodide, REPO_RAW_BASE, CLASSIFIER_FILES, "/lib", null);
      pyodide.runPython(`
import sys
for p in ["/lib", "/lib/judgment", "/lib/feedback"]:
    if p not in sys.path:
        sys.path.insert(0, p)
`);
      LabPyodide.markLoaded("p03");
    }
    return pyodide;
  }

  async function classifyAnswer(category, answerText, level) {
    const pyodide = await ensureClassifiers();
    pyodide.globals.set("_category", category);
    pyodide.globals.set("_answer", answerText);
    pyodide.globals.set("_level", level);
    pyodide.runPython(`
import json
if _category == "cognition-isolation":
    from isolation_classifier import classify_justification
    _r = classify_justification(_answer)
    _n = len(_r["matched_categories"])
    _verdict = "surface" if _n == 0 else ("partial" if _n == 1 else "defended")
else:
    from reflection_signal import evaluate_reflection
    _r = evaluate_reflection(_answer)
    if _level == "reflection":
        if not _r["required_ok"]:
            _verdict = "surface"
        elif _r["optional_matches"] < _r["min_optional_required"]:
            _verdict = "partial"
        else:
            _verdict = "defended"
    else:
        _n = _r["optional_matches"]
        _verdict = "surface" if _n == 0 else ("partial" if _n == 1 else "defended")
_classify_result = json.dumps({"verdict": _verdict, "raw": _r})
`);
    return JSON.parse(pyodide.globals.get("_classify_result"));
  }

  // D176: Team-IZ 검증세션 패턴 -- verdict는 세션 중 화면에 절대 안 보여준다(분류 자체는
  // 여전히 내부적으로 진행 로직에 쓰임, 표시만 안 함). 최종 채점은 "리포트 보기" 클릭 후
  // renderResults()에서만 노출.
  function appendTranscriptEntry(level, question, answer) {
    const el = document.getElementById("p03-transcript");
    const div = document.createElement("div");
    div.className = "finding-card";
    div.innerHTML = `<div class="fid">[${LabApp.escapeHtml(level.toUpperCase())}]</div>
      <div><b>질문:</b> ${LabApp.escapeHtml(question)}</div>
      ${answer ? `<div style="margin-top:6px;"><b>답변:</b> ${LabApp.escapeHtml(answer)}</div>` : ""}`;
    el.appendChild(div);
  }

  function waitForAnswer() {
    const row = document.getElementById("p03-answer-row");
    const input = document.getElementById("p03-answer-input");
    row.classList.remove("hidden");
    input.value = "";
    input.focus();
    return new Promise((resolve) => { pendingAnswerResolve = resolve; });
  }

  function wireAnswerSubmit() {
    document.getElementById("p03-submit-answer").addEventListener("click", () => {
      const input = document.getElementById("p03-answer-input");
      const val = input.value.trim();
      if (!val || !pendingAnswerResolve) return;
      document.getElementById("p03-answer-row").classList.add("hidden");
      const resolve = pendingAnswerResolve;
      pendingAnswerResolve = null;
      resolve(val);
    });
  }

  // D176: display-only countdown next to the live transcript -- reuses LabApp.formatElapsed
  // (already mm:ss) rather than re-implementing the same formatting. Deliberately never
  // force-ends the session at 0 (see prompt_manifest.json p03-6 note): aborting mid-LLM-call
  // or mid-answer-typing risks losing a teammate's real answer, which this tool has
  // consistently treated as worse than an over-long session (D162's whole reason for
  // existing was "never silently lose output").
  // D182: real user testing caught it ticking down during classifier loading + LLM question-
  // generation latency, before the human had even SEEN the question yet -- burning their
  // "answer time" budget on backend work they have zero control over. Split into initCountdown
  // (sets the total, shows it, does NOT tick) + resumeCountdown/pauseCountdown, called right
  // around waitForAnswer() so the clock only actually counts down while a human is reading/
  // thinking/typing -- which is what a session timer for ANSWERING should measure.
  let countdownRemainingMs = 0;
  let countdownResumedAtMs = null;

  function initCountdown(totalMinutes) {
    stopCountdown();
    const el = document.getElementById("p03-countdown");
    if (!el || !totalMinutes) return;
    countdownRemainingMs = totalMinutes * 60 * 1000;
    el.textContent = `남은 ${LabApp.formatElapsed(countdownRemainingMs)}`;
    el.classList.remove("p03-countdown-over");
  }

  function resumeCountdown() {
    const el = document.getElementById("p03-countdown");
    if (!el || countdownIntervalId || countdownRemainingMs <= 0) return; // not configured, already running, or exhausted
    countdownResumedAtMs = Date.now();
    const tick = () => {
      const elapsed = Date.now() - countdownResumedAtMs;
      const remainingMs = Math.max(0, countdownRemainingMs - elapsed);
      el.textContent = `남은 ${LabApp.formatElapsed(remainingMs)}`;
      el.classList.toggle("p03-countdown-over", remainingMs <= 0);
    };
    tick();
    countdownIntervalId = setInterval(tick, 1000);
  }

  function pauseCountdown() {
    if (!countdownIntervalId) return;
    clearInterval(countdownIntervalId);
    countdownIntervalId = null;
    if (countdownResumedAtMs !== null) {
      countdownRemainingMs = Math.max(0, countdownRemainingMs - (Date.now() - countdownResumedAtMs));
      countdownResumedAtMs = null;
    }
  }

  function stopCountdown() {
    if (countdownIntervalId) { clearInterval(countdownIntervalId); countdownIntervalId = null; }
    countdownRemainingMs = 0;
    countdownResumedAtMs = null;
    const el = document.getElementById("p03-countdown");
    if (el) { el.textContent = ""; el.classList.remove("p03-countdown-over"); }
  }

  function buildLevelPrompt(level, finding, codeContext, transcript, classification) {
    const codeBlock = codeContext ? `\n## 실제 코드\n\`\`\`\n${codeContext}\n\`\`\`\n` : "";
    const headerStage = LabApp.getStage("p03", "p03-1");
    const header = LabApp.fillTemplate(headerStage.shared_header, { finding_text: finding.finding || "", finding_file: finding.file || "", code_block: codeBlock });

    if (level === "l1") return header + LabApp.resolveTemplate("p03", "p03-1", "level_template");

    const transcriptText = transcript.map((t) => `[${t.level.toUpperCase()}] 질문: ${t.question}\n[${t.level.toUpperCase()}] 학생 답변: ${t.answer}`).join("\n");
    const verdictNote = { surface: "표면적(근거·구체성 부족)", partial: "부분적(일부 근거는 있으나 아직 충분히 깊지 않음)" }[classification.verdict] || classification.verdict;
    const stageId = { l2: "p03-2", l3: "p03-3", reflection: "p03-4" }[level];
    return header + LabApp.fillTemplate(LabApp.resolveTemplate("p03", stageId, "level_template"), { transcript: transcriptText, verdict_note: verdictNote });
  }

  async function generateQuestion(level, finding, codeContext, transcript, classification, maxAttempts) {
    // D182: falls through to the top-level toggle (selectedModel) now, same precedence as
    // P01's model resolution -- p03-1 has no manifest `model` param at all (never did), so
    // this was already effectively "shared default only" before; now it's "toggle" instead.
    const model = LabApp.resolveParam("p03", "p03-1", "model") || selectedModel;
    const prompt = buildLevelPrompt(level, finding, codeContext, transcript, classification);
    const tool = { name: "ask_question", description: "학생에게 던질 질문 하나를 생성한다.", input_schema: { type: "object", properties: { question: { type: "string" } }, required: ["question"] } };
    const result = await LabLLM.chatTool({ model, messages: [{ role: "user", content: prompt }], tool, maxTokens: 2048, maxAttempts });
    return result.question;
  }

  async function gradeAnswer(finding, question, answer, maxAttempts) {
    const stage = LabApp.getStage("p03", "p03-7");
    const rubric = (LabApp.getOverride("p03", "p03-7") || {}).rubric || stage.rubric;
    const rubricOverridden = Boolean((LabApp.getOverride("p03", "p03-7") || {}).rubric_overridden);
    let rubricBlock = "";
    for (const [axis, levels] of Object.entries(rubric)) {
      rubricBlock += `### ${axis}\n`;
      for (const score of ["5", "4", "3", "2", "1"]) rubricBlock += `  ${score}점: ${levels[score]}\n`;
    }
    const userMsg = LabApp.fillTemplate(LabApp.resolveTemplate("p03", "p03-7", "user_template"), {
      rubric_block: rubricBlock, finding_text: finding.finding || "", finding_file: finding.file || "", question, answer,
    });
    const axes = Object.keys(rubric);
    const tool = {
      name: "grade_interview_answer",
      description: "학생 답변을 FR-04-01 5축 루브릭으로 채점한다.",
      input_schema: { type: "object", properties: Object.fromEntries(axes.map((a) => [a, { type: "object", properties: { score: { type: "integer" }, evidence: { type: "string" } }, required: ["score", "evidence"] }])), required: axes },
    };
    // D182: p03-7's manifest `model` param was removed (see prompt_manifest.json) so this
    // falls through to the top-level toggle -- previously the manifest's fixed default would
    // have won here every time via resolveParam's precedence, making a toggle pointless
    // (same lesson D154 already applied to P01's stage-level model param).
    const model = LabApp.resolveParam("p03", "p03-7", "model") || selectedModel;
    const grades = await LabLLM.chatTool({ model, messages: [{ role: "user", content: userMsg }], tool, maxTokens: 2048, maxAttempts });
    return { grades, rubric_overridden: rubricOverridden };
  }

  async function run() {
    const pipelineId = "p03";
    if (!selectedFinding) { LabApp.setStatus(pipelineId, "먼저 finding을 불러오고 선택하세요", "error"); return; }
    if (!LabConfig.get("nvidia-key") || !LabConfig.get("proxy-url")) {
      LabApp.setStatus(pipelineId, "NVIDIA 키 + 프록시 URL이 필요합니다", "error");
      return;
    }
    document.getElementById("p03-session").classList.remove("hidden");
    document.getElementById("p03-transcript").innerHTML = "";
    document.getElementById("p03-progress").textContent = "질문 -/-";
    selectedContextIndex = 0;
    renderCodePanel();
    wireAnswerSubmit();
    LabApp.setStatus(pipelineId, "진행 중...", "running");
    const startedAt = new Date();
    LabApp.startTimer(pipelineId);
    const sessionTimeoutMinutes = LabApp.resolveParam("p03", "p03-6", "session_timeout_minutes") || 0;
    initCountdown(sessionTimeoutMinutes);
    try {
      LabApp.log(pipelineId, `모델: ${selectedModel}`);
      await ensureClassifiers((msg) => LabApp.log(pipelineId, msg));
      const category = findingCategory(selectedFinding.id);

      // D176: fixes a pre-existing bug -- generateQuestion() was always called with a
      // hardcoded null codeContext, so every P03 question this tool ever generated was
      // written with zero real-code visibility no matter how the finding was loaded.
      // codeContext now flows from the real P02 file content (when the finding arrived via
      // the new "인터뷰 시작" connector) through the SAME char cap the real pipeline's own
      // manifest already specifies (p03-1.truncation.code_context) -- reusing an
      // already-established number instead of inventing one. D181: now built from ALL
      // matched files (buildCombinedCodeContext), not just the first -- see its comment.
      const stage1 = LabApp.getStage("p03", "p03-1");
      const codeCap = stage1 && stage1.truncation && stage1.truncation.code_context;
      const codeContext = buildCombinedCodeContext(pendingCodeContexts, codeCap);
      LabApp.log(pipelineId, codeContext
        ? `코드 컨텍스트 포함 (${pendingCodeContexts.length}개 파일, 총 ${codeContext.length}자)`
        : "코드 컨텍스트 없음 -- 질문이 파일 내용 없이 생성됨");

      const transcript = [];
      let verdict = "exhausted_at_cap";
      const maxTurns = LabApp.resolveParam("p03", "p03-6", "max_turns") || 4;
      const totalTurns = Math.min(LEVELS.length, maxTurns);

      for (let i = 0; i < totalTurns; i++) {
        const level = LEVELS[i];
        document.getElementById("p03-progress").textContent = `질문 ${i + 1}/${totalTurns}`;
        const prevClassification = transcript.length ? transcript[transcript.length - 1].classification : null;
        const genAttempts = await resolveMaxAttempts(pipelineId);
        LabApp.log(pipelineId, `${level.toUpperCase()} 질문 생성 중...`);
        const question = await generateQuestion(level, selectedFinding, codeContext, transcript, prevClassification, genAttempts);
        appendTranscriptEntry(level, question, null);
        LabApp.log(pipelineId, "답변 대기 중...");
        resumeCountdown(); // D182: only start ticking once the human can actually see+answer this question
        const answer = await waitForAnswer();
        pauseCountdown();
        LabApp.log(pipelineId, "답변 분류 중 (결정론적 분류기, LLM 아님)...");
        const classification = await classifyAnswer(category, answer, level);
        transcript.push({ level, question, answer, classification });
        appendTranscriptEntry(level, question, answer);
        if (classification.verdict === "defended") { verdict = "defended"; break; }
      }

      LabApp.log(pipelineId, "5축 채점 중...");
      const last = transcript[transcript.length - 1];
      const gradeAttempts = await resolveMaxAttempts(pipelineId);
      const { grades, rubric_overridden } = await gradeAnswer(selectedFinding, last.question, last.answer, gradeAttempts);

      const finishedAt = new Date();
      LabApp.stopTimer(pipelineId);
      stopCountdown();
      LabApp.setStatus(pipelineId, "완료", "done");
      const result = { finding: selectedFinding, verdict, turns: transcript.length, transcript, grades, rubric_overridden };
      renderResults(result); // D184: shown immediately now, no separate "리포트 보기" click required -- see the module-level comment on why
      await maybeSaveRun(result, startedAt, finishedAt);
    } catch (err) {
      LabApp.stopTimer(pipelineId);
      stopCountdown();
      console.error(err);
      LabApp.setStatus(pipelineId, `오류: ${err.message}`, "error");
      LabApp.log(pipelineId, `오류: ${err.message}`);
      await LabApp.saveFailedRun("p03", selectedModel, err, startedAt); // D182: record what was actually selected, not the shared default
    }
  }

  function renderResults(result) {
    let html = `<p>verdict: <b>${LabApp.escapeHtml(result.verdict)}</b> · ${result.turns}턴${result.rubric_overridden ? ' · <span style="color:var(--status-blocked);">rubric_overridden</span>' : ""}</p>`;
    html += `<div class="param-grid">`;
    for (const [axis, g] of Object.entries(result.grades)) {
      html += `<div class="finding-card"><div class="fid">${LabApp.escapeHtml(axis)}: ${LabApp.escapeHtml(String(g.score))}점</div><div>${LabApp.escapeHtml(g.evidence || "")}</div></div>`;
    }
    html += `</div>`;
    html += LabApp.jsonResultBlock("원본 JSON", result, "p03-result.json");
    LabApp.showResults(html);
  }

  async function maybeSaveRun(result, startedAt, finishedAt) {
    if (!LabDB.isConfigured()) {
      LabApp.log("p03", "Supabase 미설정 — 결과는 화면에만 표시됨");
      return;
    }
    try {
      await LabDB.saveRun({
        pipeline: "p03",
        model: selectedModel, // D182: was always the shared default regardless of what actually ran
        input_meta: { finding_id: result.finding.id },
        overrides: {},
        rubric_overridden: result.rubric_overridden,
        artifacts: [{ kind: "transcript", content: result.transcript }, { kind: "grades", content: result.grades }],
        started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
      });
      LabApp.log("p03", `결과가 팀 DB에 저장됨 (소요시간 ${LabApp.formatElapsed(finishedAt - startedAt)})`);
    } catch (err) {
      LabApp.log("p03", `DB 저장 실패(결과는 화면에 남아있음): ${err.message}`);
    }
  }

  LabApp.registerRunner("p03", { renderInput, run });
  return { renderInput, run, loadFindingFromP02 };
})();
