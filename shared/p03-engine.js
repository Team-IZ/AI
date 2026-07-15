// Ported from Pipeline Lab's p03-runner.js (popixoxipop-collab/Code_reviewer_with_feedback,
// docs/lab/p03-runner.js) via copy-then-subtract, NOT re-derived from a summary -- this is
// the most delicate file in the port (D168-D183's turn-loop sequencing, rate-limit
// awareness, and the D182 countdown-timing fix all live here), so every change below is
// mechanical and individually justified, and everything else is byte-identical to the
// original:
//
//   1. run() takes explicit {finding, codeContexts, model} instead of module-level
//      selectedFinding/pendingCodeContexts/selectedModel -- there is no DOM to read state
//      from on a fresh page load, and module-level `let`s meant to persist across calls
//      don't survive a page navigation. generateQuestion()/gradeAnswer()/maybeSaveRun() gained
//      an explicit `model` parameter for the same reason (each used to fall through to the
//      module-level selectedModel).
//   2. Every LabApp.log/setStatus/startTimer/stopTimer call -> the matching hooks.* call
//      (onProgress/onStatus/onRunStart/onRunEnd) -- see run()'s own inline comments for the
//      exact mapping.
//   3. renderInput()/renderCodePanel()/appendTranscriptEntry() and loadFindingFromP02() are
//      deleted entirely -- the page draws its own Team-IZ-styled finding/model UI, code
//      panel, and chat transcript, and owns the sessionStorage handoff (there's no more
//      same-page P02Runner->P03Runner tab-switch to pre-fill DOM for). Two hooks replace
//      appendTranscriptEntry's two original call sites: hooks.onQuestion(...) where the
//      original showed the question with no answer yet, hooks.onAnswerRecorded(...) where
//      the original re-appended a second card with the answer filled in -- the ORDER these
//      fire in (question shown -> countdown resumes -> answer awaited -> countdown pauses ->
//      classified -> recorded) is preserved exactly; only how each moment is drawn is
//      page-owned now, which fits Team-IZ's separate question/answer chat bubbles more
//      naturally than the original's two-cards-per-turn shape anyway.
//   4. waitForAnswer()/wireAnswerSubmit()/pendingAnswerResolve -> replaced by an injected
//      hooks.getAnswer({level, question}) -> Promise<string>, called at the exact same point
//      in the loop. The original's exact bracket -- resumeCountdown() immediately before,
//      pauseCountdown() immediately after -- is preserved as hooks.countdown.resume()/
//      .pause() around the SAME await, since this bracket IS D182's fix (only count down
//      while a human can actually see+answer the question, not during backend latency).
//   5. The countdown quartet (initCountdown/resumeCountdown/pauseCountdown/stopCountdown,
//      plus their 3 module-level `let`s) became createCountdownController(onTick) -- a
//      DOM-free factory whose returned {start,resume,pause,stop} do the exact same
//      remaining-time arithmetic, calling onTick(remainingMs, isOver) instead of writing
//      to #p03-countdown directly (onTick(null, false) on stop(), matching the original
//      stopCountdown's el.textContent = "" clear-the-display behavior specifically, as
//      opposed to showing "0 remaining"). The page owns creating one controller instance
//      and rendering what onTick reports.
//   6. resolveMaxAttempts() no longer takes a pipelineId or call LabApp.log directly -- it
//      returns a plain {maxAttempts, elevated, count, threshold, scopeNote} struct, and the
//      exact original log message ("⚠ 현재 트래픽 ...") moved to run()'s two call sites,
//      built from that struct. The underlying rate-check (DebugTraffic.getCurrentRate(),
//      the threshold comparison, ELEVATED_MAX_ATTEMPTS) is unchanged.
//   7. renderResults() is deleted -- run() returns the result object instead of rendering it,
//      so the page can hand it to session-state.js and navigate to result.html.
//   8. run() rethrows the error after reporting it via hooks (the original never did,
//      because there was nothing for the same-page caller to do differently on success vs
//      failure -- now the caller needs to know before deciding whether to navigate to
//      result.html). The two pre-flight guards (no finding selected / no NVIDIA key+proxy)
//      still throw BEFORE hooks.onRunStart()/the try block, exactly like the original's
//      early `return` before startTimer() was ever called -- no failed-run save happens for
//      these, same as before.
//   9. ACKNOWLEDGED BEHAVIOR CHANGE (not preserved): the original re-read the module-level
//      `selectedModel` fresh on every generateQuestion()/gradeAnswer() call, so changing the
//      model-toggle mid-interview (between turns) took effect on the next turn. `model` here
//      is a plain run(input, hooks) parameter, frozen for the whole call -- switching models
//      mid-flight is no longer supported. Judged a minor, rarely-exercised edge case not
//      worth threading a live-mutable model reference through every call site for; the page
//      is expected to disable its model selector once hooks.onRunStart() fires, so the UI
//      itself doesn't imply a capability that no longer exists.
//
// P03 v1 is human-in-the-loop only (persona/AI-answerer mode is out of scope here). Question
// generation + grading are real NVIDIA calls (same manifest prompts as P01/P02). Answer
// classification (surface/partial/defended) is NOT an LLM call -- it's the same
// deterministic regex classifiers P02 already proved run fine in Pyodide.
const P03Engine = (() => {
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
  // D181: unmeasured/provisional -- see resolveMaxAttempts()'s comment for the reasoning.
  const ELEVATED_RATE_THRESHOLD = 30;
  const ELEVATED_MAX_ATTEMPTS = 5;

  function findingCategory(findingId) {
    // mirrors feedback/evidence_bridge.py::finding_category -- id prefix before the first ':'
    return (findingId || "").split(":")[0];
  }

  // D181: concatenates every candidate file (labeled with its path) into one prompt-ready
  // string, then applies the manifest's own existing p03-1.truncation.code_context cap to
  // the WHOLE combined text -- not proportionally splitting per file, a deliberately simple
  // choice: if the first file's content alone exceeds the cap, later files get cut, which is
  // an acceptable provisional behavior, not a silent data-loss regression.
  function buildCombinedCodeContext(contexts, cap) {
    if (!contexts || !contexts.length) return null;
    const labeled = contexts.map((c) => `--- ${c.path} ---\n${c.content}`).join("\n\n");
    return cap ? labeled.slice(0, cap) : labeled;
  }

  // D181: shared 40rpm ceiling has real prior incidents from P01's chunk bursts (D163-171).
  // P03's own calls are never a burst by themselves (one in-flight call at a time,
  // human-paced between turns) -- the actual risk is several teammates' P03 sessions
  // overlapping against the same shared proxy/key, invisible to each other without this
  // check. Reuses DebugTraffic's own already-throttled rolling count and, if it's elevated,
  // requests more retry budget via the same x-max-attempts mechanism D169 built for P01.
  // ELEVATED_RATE_THRESHOLD/ELEVATED_MAX_ATTEMPTS are both unmeasured/provisional -- 75% of
  // the documented ~40rpm ceiling, and one more than the worker's own MAX_ATTEMPTS=3 default.
  // Change #6: returns a struct instead of logging directly; caller builds the message.
  async function resolveMaxAttempts() {
    if (typeof DebugTraffic === "undefined" || !DebugTraffic.getCurrentRate) return { maxAttempts: undefined, elevated: false };
    const { count, isServerWide, threshold } = await DebugTraffic.getCurrentRate();
    if (count < ELEVATED_RATE_THRESHOLD) return { maxAttempts: undefined, elevated: false, count, threshold };
    const scopeNote = isServerWide ? "" : " (이 탭 기준만 -- 다른 팀원 트래픽은 안 잡힘)";
    return { maxAttempts: ELEVATED_MAX_ATTEMPTS, elevated: true, count, threshold, scopeNote };
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

  // Change #5: factory replacing initCountdown/resumeCountdown/pauseCountdown/stopCountdown
  // and their 3 module-level `let`s. Same remaining-time arithmetic, DOM-free -- calls
  // onTick(remainingMs, isOver) instead of writing #p03-countdown directly.
  // onTick(null, false) on stop() specifically signals "clear the display" (the original
  // stopCountdown's el.textContent = ""), distinct from onTick(0, true) which means "show
  // 00:00, over".
  function createCountdownController(onTick) {
    let remainingMs = 0;
    let resumedAtMs = null;
    let intervalId = null;

    function emit() {
      const remaining = resumedAtMs !== null ? Math.max(0, remainingMs - (Date.now() - resumedAtMs)) : remainingMs;
      onTick(remaining, remaining <= 0);
    }

    function start(totalMinutes) {
      stop();
      if (!totalMinutes) return;
      remainingMs = totalMinutes * 60 * 1000;
      emit();
    }

    function resume() {
      if (intervalId || remainingMs <= 0) return; // not configured, already running, or exhausted
      resumedAtMs = Date.now();
      emit();
      intervalId = setInterval(emit, 1000);
    }

    function pause() {
      if (!intervalId) return;
      clearInterval(intervalId);
      intervalId = null;
      if (resumedAtMs !== null) {
        remainingMs = Math.max(0, remainingMs - (Date.now() - resumedAtMs));
        resumedAtMs = null;
      }
    }

    function stop() {
      if (intervalId) { clearInterval(intervalId); intervalId = null; }
      remainingMs = 0;
      resumedAtMs = null;
      onTick(null, false);
    }

    return { start, resume, pause, stop };
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

  // Change #1: takes `model` explicitly (was module-level selectedModel fallback).
  async function generateQuestion(level, finding, codeContext, transcript, classification, maxAttempts, model) {
    // D182: falls through to the top-level toggle (model) -- p03-1 has no manifest `model`
    // param at all (never did), so this was already effectively "shared default only"
    // before; now it's "toggle" instead.
    const resolvedModel = LabApp.resolveParam("p03", "p03-1", "model") || model;
    const prompt = buildLevelPrompt(level, finding, codeContext, transcript, classification);
    const tool = { name: "ask_question", description: "학생에게 던질 질문 하나를 생성한다.", input_schema: { type: "object", properties: { question: { type: "string" } }, required: ["question"] } };
    const result = await LabLLM.chatTool({ model: resolvedModel, messages: [{ role: "user", content: prompt }], tool, maxTokens: 2048, maxAttempts });
    return result.question;
  }

  // Change #1: takes `model` explicitly (was module-level selectedModel fallback).
  async function gradeAnswer(finding, question, answer, maxAttempts, model) {
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
    // D182: p03-7's manifest `model` param was removed so this falls through to the
    // top-level toggle -- previously the manifest's fixed default would have won here
    // every time via resolveParam's precedence, making a toggle pointless.
    const resolvedModel = LabApp.resolveParam("p03", "p03-7", "model") || model;
    const grades = await LabLLM.chatTool({ model: resolvedModel, messages: [{ role: "user", content: userMsg }], tool, maxTokens: 2048, maxAttempts });
    return { grades, rubric_overridden: rubricOverridden };
  }

  // Change #1/#2/#3/#4/#6/#7/#8 -- see file header for the full mapping. Turn-loop
  // sequencing itself (prevClassification feeding the next question, the verdict sentinel
  // defaulting to "exhausted_at_cap" and only flipping on an in-loop break, `last` always
  // being whichever turn the loop actually stopped on) is untouched.
  //
  // input: { finding, codeContexts, model }
  // hooks: { onStatus(text,kind), onProgress(msg), onRunStart(), onRunEnd(elapsedMs),
  //          onQuestion({level,question,turnIndex,totalTurns}),
  //          getAnswer({level,question}) -> Promise<string>,
  //          onAnswerRecorded({level,question,answer,classification}),
  //          countdown: {start(totalMinutes), resume(), pause(), stop()} }
  async function run(input, hooks) {
    const { finding, codeContexts, model } = input;
    if (!finding) {
      hooks.onStatus("먼저 finding을 불러오고 선택하세요", "error");
      throw new Error("먼저 finding을 불러오고 선택하세요");
    }
    if (!LabConfig.get("nvidia-key") || !LabConfig.get("proxy-url")) {
      hooks.onStatus("NVIDIA 키 + 프록시 URL이 필요합니다", "error");
      throw new Error("NVIDIA 키 + 프록시 URL이 필요합니다");
    }

    hooks.onStatus("진행 중...", "running");
    const startedAt = new Date();
    hooks.onRunStart();
    const sessionTimeoutMinutes = LabApp.resolveParam("p03", "p03-6", "session_timeout_minutes") || 0;
    hooks.countdown.start(sessionTimeoutMinutes);
    try {
      hooks.onProgress(`모델: ${model}`);
      await ensureClassifiers((msg) => hooks.onProgress(msg));
      const category = findingCategory(finding.id);

      // D176/D181: codeContext flows from the real P02 file content through the same char
      // cap the real pipeline's own manifest already specifies (p03-1.truncation.code_context),
      // built from ALL matched files (buildCombinedCodeContext), not just the first.
      const stage1 = LabApp.getStage("p03", "p03-1");
      const codeCap = stage1 && stage1.truncation && stage1.truncation.code_context;
      const codeContext = buildCombinedCodeContext(codeContexts, codeCap);
      hooks.onProgress(codeContext
        ? `코드 컨텍스트 포함 (${codeContexts.length}개 파일, 총 ${codeContext.length}자)`
        : "코드 컨텍스트 없음 -- 질문이 파일 내용 없이 생성됨");

      const transcript = [];
      let verdict = "exhausted_at_cap";
      const maxTurns = LabApp.resolveParam("p03", "p03-6", "max_turns") || 4;
      const totalTurns = Math.min(LEVELS.length, maxTurns);

      for (let i = 0; i < totalTurns; i++) {
        const level = LEVELS[i];
        const prevClassification = transcript.length ? transcript[transcript.length - 1].classification : null;
        const genAttemptInfo = await resolveMaxAttempts();
        if (genAttemptInfo.elevated) hooks.onProgress(`⚠ 현재 트래픽 ${genAttemptInfo.count}/${genAttemptInfo.threshold}${genAttemptInfo.scopeNote} -- 재시도 여유를 ${ELEVATED_MAX_ATTEMPTS}회로 늘려서 요청`);
        hooks.onProgress(`${level.toUpperCase()} 질문 생성 중...`);
        const question = await generateQuestion(level, finding, codeContext, transcript, prevClassification, genAttemptInfo.maxAttempts, model);
        hooks.onQuestion({ level, question, turnIndex: i, totalTurns });
        hooks.onProgress("답변 대기 중...");
        hooks.countdown.resume(); // D182: only start ticking once the human can actually see+answer this question
        const answer = await hooks.getAnswer({ level, question });
        hooks.countdown.pause();
        hooks.onProgress("답변 분류 중 (결정론적 분류기, LLM 아님)...");
        const classification = await classifyAnswer(category, answer, level);
        transcript.push({ level, question, answer, classification });
        hooks.onAnswerRecorded({ level, question, answer, classification });
        if (classification.verdict === "defended") { verdict = "defended"; break; }
      }

      hooks.onProgress("5축 채점 중...");
      const last = transcript[transcript.length - 1];
      const gradeAttemptInfo = await resolveMaxAttempts();
      if (gradeAttemptInfo.elevated) hooks.onProgress(`⚠ 현재 트래픽 ${gradeAttemptInfo.count}/${gradeAttemptInfo.threshold}${gradeAttemptInfo.scopeNote} -- 재시도 여유를 ${ELEVATED_MAX_ATTEMPTS}회로 늘려서 요청`);
      const { grades, rubric_overridden } = await gradeAnswer(finding, last.question, last.answer, gradeAttemptInfo.maxAttempts, model);

      const finishedAt = new Date();
      hooks.onRunEnd(finishedAt - startedAt);
      hooks.countdown.stop();
      hooks.onStatus("완료", "done");
      const result = { finding, verdict, turns: transcript.length, transcript, grades, rubric_overridden };
      await maybeSaveRun(result, startedAt, finishedAt, model, hooks);
      return result;
    } catch (err) {
      hooks.onRunEnd(new Date() - startedAt);
      hooks.countdown.stop();
      console.error(err);
      hooks.onStatus(`오류: ${err.message}`, "error");
      hooks.onProgress(`오류: ${err.message}`);
      await LabApp.saveFailedRun("p03", model, err, startedAt, hooks.onProgress);
      throw err;
    }
  }

  // Change #1: `model` param replaces the original's module-level selectedModel read.
  async function maybeSaveRun(result, startedAt, finishedAt, model, hooks) {
    if (!LabDB.isConfigured()) {
      hooks.onProgress("Supabase 미설정 — 결과는 화면에만 표시됨");
      return;
    }
    try {
      await LabDB.saveRun({
        pipeline: "p03",
        model,
        input_meta: { finding_id: result.finding.id },
        overrides: {},
        rubric_overridden: result.rubric_overridden,
        artifacts: [{ kind: "transcript", content: result.transcript }, { kind: "grades", content: result.grades }],
        started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
      });
      hooks.onProgress(`결과가 팀 DB에 저장됨 (소요시간 ${LabApp.formatElapsed(finishedAt - startedAt)})`);
    } catch (err) {
      hooks.onProgress(`DB 저장 실패(결과는 화면에 남아있음): ${err.message}`);
    }
  }

  return {
    findingCategory, buildCombinedCodeContext, createCountdownController,
    run, LEVELS,
  };
})();
