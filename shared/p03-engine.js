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

  // D199: `classification` param dropped -- verdict_note is now the FULL turn-by-turn
  // trail (buildVerdictTrail(transcript)), not just the immediately preceding turn's
  // verdict. See buildVerdictTrail()'s comment for why.
  // D200: new trailing `factCheckAvailable` -- when true (repo identity known AND this is
  // the first dedup attempt, see generateQuestion()), appends the manifest's per-stage
  // fact_check_hint telling the model it may call list_files/read_file before answering.
  // Never appended for l1 (no prior claim exists yet to fact-check).
  function buildLevelPrompt(level, finding, codeContext, transcript, extraBanned, factCheckAvailable) {
    const codeBlock = codeContext ? `\n## 실제 코드\n\`\`\`\n${codeContext}\n\`\`\`\n` : "";
    const headerStage = LabApp.getStage("p03", "p03-1");
    const header = LabApp.fillTemplate(headerStage.shared_header, { finding_text: finding.finding || "", finding_file: finding.file || "", code_block: codeBlock });

    if (level === "l1") return header + LabApp.resolveTemplate("p03", "p03-1", "level_template");

    const transcriptText = transcript.map((t) => `[${t.level.toUpperCase()}] 질문: ${t.question}\n[${t.level.toUpperCase()}] 학생 답변: ${t.answer}`).join("\n");
    const verdictNote = buildVerdictTrail(transcript);
    const stageId = { l2: "p03-2", l3: "p03-3", reflection: "p03-4" }[level];
    let prompt = header + LabApp.fillTemplate(LabApp.resolveTemplate("p03", stageId, "level_template"), { transcript: transcriptText, verdict_note: verdictNote });
    // D190: only non-empty on a dedup-triggered retry within the SAME level (see
    // generateQuestion) -- `transcript` above already carries prior LEVELS' questions via
    // the "이미 물어본 질문"(D188) section, but a rejected same-level attempt never gets
    // pushed to `transcript` (that only happens after the turn's answer comes back), so it
    // has to be listed separately here.
    // D206: user-observed gap -- merely SAYING "겹침 감지됨" without quoting which prior
    // question it matched let the model rationalize the rejected candidate was "different
    // enough" and often regenerate something just as similar. Now each entry pairs the
    // rejected candidate with the SPECIFIC prior question isDuplicateQuestion() actually
    // matched it against (`extraBanned[i].dupOf`), both quoted verbatim side by side --
    // showing the overlap directly instead of only asserting it exists.
    if (extraBanned && extraBanned.length) {
      prompt += `\n\n## 방금 생성했으나 반려된 질문 (아래 [생성한 질문]과 [겹치는 이전 질문]을 직접 비교해보면 겹침이 보일 것입니다) — 이것과도 겹치면 안 됩니다\n`
        + extraBanned.map((r, i) => `${i + 1}. [생성한 질문] ${r.candidate}\n   [겹치는 이전 질문] ${r.dupOf}`).join("\n");
    }
    if (factCheckAvailable) {
      const hint = LabApp.resolveTemplate("p03", stageId, "fact_check_hint");
      if (hint) prompt += `\n\n## 실시간 재확인 안내\n${hint}`;
    }
    return prompt;
  }

  // D190: trainee가 이번 세션에서 목격한 재발(스크린샷) -- D188은 p03-2/3/4 프롬프트
  // 문구만 고쳤는데, 이번엔 다른 finding(step-3.5-flash, load_api_keys)에서 L2==
  // Reflection이 다시 byte-identical하게 나옴. 확률적 생성인 이상 프롬프트만으로는
  // 특정 모델x특정 finding 조합의 재발을 완전히 막을 보장이 없다는 게 재실측 확인됨.
  //   WHY: 코드 레벨 사후검사(생성 -> 유사도 체크 -> 필요시 재생성)가 유일한 결정론적
  //   안전망 -- D188 보고서가 이미 제안했던 "defense-in-depth"를 이번 재발로 실측
  //   정당화됨에 따라 적용. 임계값 0.5는 이 세션의 실측 데이터로 보정: 이번 재발
  //   케이스(byte-identical) overlap=1.0, 같은 스크린샷의 정상 L2-vs-L3 쌍
  //   overlap=0.20, D188의 기존 데이터(진짜중복=0.92, 정상=0.06~0.22)와도 일관됨 --
  //   그 사이 넓은 안전마진.
  //   COST: 중복 감지 시 재생성 API 호출 1~2회 추가(레이턴시+비용) -- 정상 케이스가
  //   0.5를 넘지 않으므로 오탐으로 인한 불필요한 재시도는 거의 없을 것으로 예상(다만
  //   실측 anchor point 4개뿐이라 완전한 보장은 아님, 재발 시 EXIT 참고). 재시도 다
  //   소진해도 여전히 중복이면 마지막 결과를 그대로 반환(무한루프/세션 중단 방지가
  //   완전 차단보다 우선) -- 이 경우 onProgress로 경고만 표시.
  //   EXIT: 임계값(0.5)이나 재시도 횟수(2)가 실측과 안 맞으면 이 두 상수만 조정. 근본
  //   원인은 여전히 모델의 지시 미준수이므로, 특정 모델이 계속 이 가드에 걸리면
  //   D188처럼 프롬프트를 추가로 강화하거나 그 모델을 P03 기본값에서 내리는 게 근본
  //   해법(이 가드는 보험이지 근본수정 대체가 아님).
  const DEDUP_JACCARD_THRESHOLD = 0.5;
  const DEDUP_MAX_RETRIES = 2;

  function normalizeForDedup(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  // 문자 3-gram Jaccard -- 한국어는 공백 기준 단어 토큰화가 조사/어미 때문에 불안정해서
  // (형태소 분석기 없이는 "질문을"과 "질문이"가 다른 토큰이 됨), 단어 n-gram 대신 문자
  // n-gram을 쓴다(CJK 텍스트 유사도 비교의 표준 관행).
  function ngramJaccard(a, b, n = 3) {
    const na = normalizeForDedup(a), nb = normalizeForDedup(b);
    if (na.length < n || nb.length < n) return na === nb ? 1 : 0;
    const gramsA = new Set(); for (let i = 0; i <= na.length - n; i++) gramsA.add(na.slice(i, i + n));
    const gramsB = new Set(); for (let i = 0; i <= nb.length - n; i++) gramsB.add(nb.slice(i, i + n));
    let intersection = 0;
    for (const g of gramsA) if (gramsB.has(g)) intersection++;
    const union = gramsA.size + gramsB.size - intersection;
    return union === 0 ? 0 : intersection / union;
  }

  // D199: trainee가 실제 세션에서 목격한 재발(스크린샷) -- L1 질문 전체(전제 설명+실제
  // 질문 문장) 대비 L2 질문이 L1의 "질문:" 이후 문장을 글자 그대로 반복. 실측 결과
  // ngramJaccard(L1,L2) = 0.4556로 DEDUP_JACCARD_THRESHOLD(0.5) 바로 아래라 D190의
  // 가드가 아예 재시도를 시도하지도 않았음(콘솔에도 경고 없음, 무한루프 방지 로직이
  // 작동한 게 아니라 애초에 "겹침"으로 감지가 안 된 것).
  //   WHY: 근본 원인은 union 기반 Jaccard가 두 문자열 길이가 크게 다를 때(이번 케이스
  //   L2가 L1의 39% 길이) 완전 포함 관계여도 union이 커져 비율이 희석됨 -- 실측
  //   normalizeForDedup(L1).includes(normalizeForDedup(L2)) === true (L2가 L1의 완전한
  //   부분문자열)인데 Jaccard는 0.4556. Overlap coefficient(교집합/min(|A|,|B|))는 정확히
  //   이런 "포함 관계" 중복을 길이 비대칭과 무관하게 잡도록 설계된 지표 -- 같은 쌍에서
  //   1.0000 측정. D190의 기존 anchor(진짜중복 0.92/1.0, 정상 0.06~0.22)는 "비슷한
  //   길이의 거의 동일한 문장" 패턴만 다뤘고, "짧은 질문이 긴 질문의 부분집합" 패턴은
  //   D190 실측 범위 밖이었다 -- 이번 재발은 D190을 뒤집는 게 아니라 D190이 안 보던
  //   실패모드를 새로 커버하는 것.
  //   COST: OVERLAP_COEFFICIENT_THRESHOLD(0.8)는 이번 재발 케이스(1.0)만 실측 anchor로
  //   보정된 값 -- D188/D190처럼 "정상 케이스"의 overlap coefficient 실측 anchor는 아직
  //   없음(unmeasured/provisional, CLAUDE.md 데이터우선주의 명시 위반 방지 차원에서
  //   일부러 매우 보수적으로 0.8로 잡음 -- 완전 포함에 가까운 경우만 잡고, 단순히 같은
  //   finding/파일명을 반복 언급하는 정상적인 후속 질문까지 오탐할 가능성은 낮지만
  //   보장은 안 됨). 오탐 시 재생성 API 호출 추가(레이턴시+비용).
  //   EXIT: 정상 케이스에서 오탐이 재현되면 이 세션 로그에서 실측 anchor를 뽑아
  //   OVERLAP_COEFFICIENT_THRESHOLD를 재보정. 근본 원인(모델이 지시 무시하고 이전
  //   질문의 핵심 문장을 그대로 재사용)은 여전히 프롬프트 차원 해법(D188)이 1차 방어,
  //   이 가드는 2차 방어(defense-in-depth)일 뿐.
  const OVERLAP_COEFFICIENT_THRESHOLD = 0.8;

  // intersection / min(|A|,|B|) -- union 대신 더 작은 쪽 크기로 나눠서, 짧은 문자열이
  // 긴 문자열에 완전히 포함되는 경우 길이 차이와 무관하게 1.0에 가깝게 나오도록 함.
  function ngramOverlapCoefficient(a, b, n = 3) {
    const na = normalizeForDedup(a), nb = normalizeForDedup(b);
    if (na.length < n || nb.length < n) return na === nb ? 1 : 0;
    const gramsA = new Set(); for (let i = 0; i <= na.length - n; i++) gramsA.add(na.slice(i, i + n));
    const gramsB = new Set(); for (let i = 0; i <= nb.length - n; i++) gramsB.add(nb.slice(i, i + n));
    let intersection = 0;
    for (const g of gramsA) if (gramsB.has(g)) intersection++;
    const minSize = Math.min(gramsA.size, gramsB.size);
    return minSize === 0 ? 0 : intersection / minSize;
  }

  function isDuplicateQuestion(candidate, prior) {
    return ngramJaccard(candidate, prior) >= DEDUP_JACCARD_THRESHOLD
      || ngramOverlapCoefficient(candidate, prior) >= OVERLAP_COEFFICIENT_THRESHOLD;
  }

  // D199: 매 턴 질문 생성 시 그 시점까지 실제로 쌓인 transcript 전체(각 턴의 level +
  // classification.verdict)에서 직접 파생 -- 예전엔 run()이 "바로 직전 턴 하나"의
  // classification만 별도 파라미터로 넘겨받아 verdict_note를 만들었는데, transcript
  // 자체가 이미 모든 턴의 classification을 갖고 있으므로 그 정보가 중복 전달되고
  // 있었을 뿐이었다. L3/Reflection 생성 시점에 "L1은 부분적, L2는 표면적"처럼 지금까지의
  // 판정 흐름 전체를 프롬프트에 보여주면, 모델이 "이미 이 각도로 찔러봤다"는 걸 판정
  // 이력만으로도 더 명확히 인지해서 같은 각도의 질문을 반복할 유인이 준다(요청自
  // 근거: 중복 질문 완화 목적).
  function buildVerdictTrail(transcript) {
    const LABEL = { surface: "표면적(근거·구체성 부족)", partial: "부분적(일부 근거는 있으나 아직 충분히 깊지 않음)", defended: "방어됨" };
    return transcript.map((t) => `${t.level.toUpperCase()}=${LABEL[t.classification.verdict] || t.classification.verdict}`).join(", ");
  }

  // D200: live fact-check tools for L2/L3/Reflection question generation -- gives the model
  // real GitHub access to re-verify the trainee's last answer instead of only ever seeing a
  // static snippet chosen once at session start. See run()'s D200 comment and llm.js's
  // chatToolLoop() for the WHY/COST/EXIT of the overall mechanism; this block is just the
  // GitHub-specific tool schemas + executors.
  const LIST_FILES_TOOL = {
    name: "list_files",
    description: "저장소의 실제 파일 목록을 가져온다 (GitHub API git tree, 현재 브랜치 기준). 전달받은 코드 스니펫이 아니라 실제 저장소 구조를 확인할 때 사용.",
    input_schema: { type: "object", properties: {}, required: [] },
  };
  const READ_FILE_TOOL = {
    name: "read_file",
    description: "저장소의 특정 파일 하나의 실제 최신 내용을 가져온다 (GitHub API contents). 학생 답변에서 언급된 구체적 파일/함수를 재확인할 때 사용. path는 list_files로 얻은 경로여야 한다.",
    input_schema: { type: "object", properties: { path: { type: "string", description: "저장소 루트 기준 상대 경로" } }, required: ["path"] },
  };
  // v2 확장 지점: git_log(path) -- GET /repos/{owner}/{repo}/commits?path=...&sha={branch},
  // 같은 executors 패턴으로 추가 가능. 이번엔 같은 라운드 예산을 list_files/read_file과
  // 나눠 써야 하는 문제 + 커밋 이력 요약/절단 로직이 별도로 필요해 보류.
  const LIST_FILES_MAX_ENTRIES = 300; // unmeasured/provisional -- P02Engine.MAX_CONNECT_FILES 관례를 따름
  const READ_FILE_CHAR_CAP = 8000; // p03-1.truncation.code_context 관례를 따름(재확인 1회가 원본 스니펫 예산을 과도하게 넘지 않도록)

  function githubHeaders(pat) {
    const headers = { accept: "application/vnd.github+json" };
    if (pat) headers.authorization = `Bearer ${pat}`;
    return headers;
  }

  // D200: P02Engine.fetchGithubRepo()를 재사용하지 않음 -- 그건 스캔 시점에 저장소
  // 전체를 벌크로 가져오는 함수라, 세션 도중 파일 하나만 가볍게 재조회하는 이 용도엔
  // 맞지 않음. 안전에 직결되는 부분(D192 레이트리밋 탐지)만 P02Engine.githubRateLimitError로
  // 재사용하고, 나머지 헤더/요청 구성은 가볍게 미러링(이 코드베이스에서 이미 p03-runner.js가
  // 쓰는 것과 같은 관례 -- 작고 안정적인 스니펫은 크로스 파일 결합보다 가벼운 중복을 선호).
  async function toolListFiles(repoRef, pat) {
    const { owner, repo, branch } = repoRef;
    const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`, { headers: githubHeaders(pat) });
    if (!res.ok) throw (P02Engine.githubRateLimitError(res, pat) || new Error(`list_files 실패 (HTTP ${res.status})`));
    const tree = await res.json();
    const paths = (tree.tree || []).filter((t) => t.type === "blob").map((t) => t.path);
    return { paths: paths.slice(0, LIST_FILES_MAX_ENTRIES), truncated: paths.length > LIST_FILES_MAX_ENTRIES, total: paths.length };
  }

  async function toolReadFile(repoRef, pat, path) {
    const { owner, repo, branch } = repoRef;
    const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/contents/${encodeURIComponent(path)}?ref=${encodeURIComponent(branch)}`, { headers: githubHeaders(pat) });
    if (!res.ok) throw (P02Engine.githubRateLimitError(res, pat) || new Error(`read_file 실패 (${path}, HTTP ${res.status})`));
    const data = await res.json();
    if (Array.isArray(data) || data.type !== "file" || !data.content) throw new Error(`read_file: ${path}는 파일이 아니거나 내용 없음`);
    const content = decodeURIComponent(escape(atob(data.content.replace(/\n/g, ""))));
    return {
      path,
      content: content.length > READ_FILE_CHAR_CAP ? content.slice(0, READ_FILE_CHAR_CAP) + "\n...[생략]" : content,
      truncated: content.length > READ_FILE_CHAR_CAP,
    };
  }

  // D200: WHY -- L2/L3/Reflection follow-ups used to invent a hypothetical counter-example
  // against a STATIC snippet, never re-verified (the gap a reference "grounded fact-check"
  // interview transcript exposed; user directed adopting the high-cost structural fix: give
  // the question-generator genuine live tool access, not just a better prompt).
  //   COST: up to FACT_CHECK_MAX_ROUNDS+1 LLM round-trips for the fact-checked attempt
  //   instead of 1, each a real job-queue submit+poll cycle (not instant). Only attempted on
  //   attempt===0 of the dedup loop below -- dedup retries are about textual novelty, not
  //   re-verifying facts already checked once, so re-fact-checking on retry would burn GitHub
  //   rate-limit budget for no benefit. Worst case total LLM calls for one question:
  //   (FACT_CHECK_MAX_ROUNDS+1) + DEDUP_MAX_RETRIES = 3 + 2 = 5 at current constants.
  //   EXIT: if 2 rounds proves too tight (model wants list_files + 2x read_file), raise to 3
  //   -- start conservative given each round stacks real job-queue latency on top of the
  //   trainee's session_timeout_minutes countdown, not a cheap in-memory retry.
  const FACT_CHECK_MAX_ROUNDS = 2;

  // Change #1: takes `model` explicitly (was module-level selectedModel fallback).
  // D190: adds `onProgress` (optional) to surface dedup-retry warnings the same way other
  // unusual-but-handled conditions already do (e.g. the elevated-traffic warning in run()).
  // D199: dropped the separate `classification` param -- buildLevelPrompt() now derives the
  // cumulative verdict trail from `transcript` itself (which already carries every turn's
  // classification), so passing "just the last one" separately was redundant duplication.
  // D200: new trailing `repoRef` ({owner,repo,branch} or null for ZIP uploads) -- enables
  // the live fact-check tool-loop above for L2/L3/Reflection. `repoRef` absent/null is an
  // undetectable no-op (ZIP path keeps behaving exactly as before D200).
  // D204: user-observed gap -- D200's fact-check was only ever probabilistic
  // (tool_choice:"auto" let the model skip straight to ask_question) AND dedup retries
  // (attempt>0) never fact-checked at all, silently falling back to the static-snippet
  // path exactly when a question was being regenerated. Fixed by:
  //   WHY: (a) force >=1 real list_files/read_file call via chatToolLoop's new
  //   minNonTerminalRounds on the FIRST attempt only -- the model can no longer skip
  //   grounding entirely; (b) share ONE `messages` array across every dedup attempt for
  //   this turn, so a rejected/regenerated question continues the SAME conversation that
  //   already contains the real tool-call/tool-result exchange -- retries stay grounded in
  //   what was actually fetched instead of reverting to the pre-D200 snippet-only prompt.
  //   COST: worst-case LLM round trips unchanged from D200 (FACT_CHECK_MAX_ROUNDS+1 for the
  //   fact-checked path + DEDUP_MAX_RETRIES for regeneration) -- GitHub API calls actually
  //   go DOWN versus a naive "fact-check every retry too" design, since retries reuse
  //   already-fetched file contents rather than re-querying. If `chatToolLoop` throws at any
  //   point (rate limit, etc.), `factCheckBroken` latches true and every remaining attempt
  //   (this one and further retries) falls back to the plain snippet-only path for the rest
  //   of this call -- one warning, not one per attempt.
  //   EXIT: if a future model needs to re-verify facts on every retry (not just reuse them),
  //   drop the shared-`messages` reuse and re-run the full fact-check loop per attempt --
  //   revert to passing `minNonTerminalRounds: 1` on every attempt instead of only attempt 0.
  // D206: user-observed gap on top of D204 -- telling the model "this overlaps with a prior
  // question" without quoting WHICH prior question let it rationalize the rejected candidate
  // was different enough and regenerate something just as similar. `rejected` below now
  // stores {candidate, dupOf} pairs -- both texts quoted verbatim, side by side, in both the
  // shared-messages retry note and buildLevelPrompt()'s extraBanned block -- showing the
  // overlap directly instead of only asserting it exists.
  async function generateQuestion(level, finding, codeContext, transcript, maxAttempts, model, onProgress, repoRef) {
    // D182: falls through to the top-level toggle (model) -- p03-1 has no manifest `model`
    // param at all (never did), so this was already effectively "shared default only"
    // before; now it's "toggle" instead.
    const resolvedModel = LabApp.resolveParam("p03", "p03-1", "model") || model;
    const askQuestionTool = { name: "ask_question", description: "학생에게 던질 질문 하나를 생성한다.", input_schema: { type: "object", properties: { question: { type: "string" } }, required: ["question"] } };
    const priorQuestions = transcript.map((t) => t.question);
    const rejected = [];
    const pat = LabConfig.get("github-pat");
    const factCheckable = level !== "l1" && repoRef && repoRef.owner && repoRef.repo;

    // D204: shared across every dedup attempt for THIS turn -- see comment above. `null`
    // when fact-check was never possible to begin with (L1, or no repo identity).
    let messages = factCheckable
      ? [{ role: "user", content: buildLevelPrompt(level, finding, codeContext, transcript, [], true) }]
      : null;
    let factCheckBroken = !factCheckable;

    for (let attempt = 0; attempt <= DEDUP_MAX_RETRIES; attempt++) {
      let candidate;

      if (!factCheckBroken) {
        try {
          const result = await LabLLM.chatToolLoop({
            model: resolvedModel,
            messages,
            tools: [LIST_FILES_TOOL, READ_FILE_TOOL, askQuestionTool],
            executors: {
              list_files: async () => toolListFiles(repoRef, pat),
              read_file: async (args) => toolReadFile(repoRef, pat, args.path),
            },
            terminalToolName: "ask_question",
            maxRounds: FACT_CHECK_MAX_ROUNDS,
            // D204: only the FIRST attempt must actually touch GitHub -- retries reuse the
            // tool-call/tool-result messages chatToolLoop already appended to `messages` in
            // place (same array reference), so they stay fact-grounded without a second
            // live round trip.
            minNonTerminalRounds: attempt === 0 ? 1 : 0,
            maxTokens: 2048, maxAttempts, onProgress,
          });
          candidate = result.question;
        } catch (err) {
          // D200/D204: graceful degrade -- rate-limit mid-loop or any other tool-loop
          // failure falls back to the plain single-shot path for THIS and every remaining
          // attempt (not just this one). Interview must never stop for this.
          if (onProgress) onProgress(`⚠ 실시간 코드 재확인을 건너뜁니다 (${err.message}) — 이후 이 턴은 저장된 코드 스니펫만으로 질문을 생성합니다`);
          factCheckBroken = true;
        }
      }
      if (factCheckBroken) {
        const prompt = buildLevelPrompt(level, finding, codeContext, transcript, rejected, false);
        const fallback = await LabLLM.chatTool({ model: resolvedModel, messages: [{ role: "user", content: prompt }], tool: askQuestionTool, maxTokens: 2048, maxAttempts });
        candidate = fallback.question;
      }

      const dupOf = priorQuestions.find((q) => isDuplicateQuestion(candidate, q));
      if (!dupOf) return candidate;
      if (attempt === DEDUP_MAX_RETRIES) {
        if (onProgress) onProgress(`⚠ ${level.toUpperCase()} 질문이 이전 질문과 유사해 보이지만 재생성 한도(${DEDUP_MAX_RETRIES}회) 소진 — 그대로 진행`);
        return candidate;
      }
      if (onProgress) onProgress(`⚠ ${level.toUpperCase()} 질문이 이전 질문과 겹쳐 재생성 중 (${attempt + 1}/${DEDUP_MAX_RETRIES})...`);
      // D206: store the SPECIFIC prior question this candidate matched (`dupOf`) alongside
      // it, not just the candidate -- see buildLevelPrompt()'s D206 comment for the WHY.
      rejected.push({ candidate, dupOf });
      // D204/D206: keep the fact-check conversation thread informed of the rejection too, so
      // a reused-messages retry knows not to repeat it (mirrors buildLevelPrompt's own
      // extraBanned section for the non-fact-check path) -- and, per D206, quotes both the
      // rejected candidate AND the specific prior question it duplicated side by side rather
      // than only asserting an overlap exists.
      if (messages) {
        messages.push({
          role: "user",
          content: `방금 생성한 질문이 이전 질문과 겹칩니다. 아래 [생성한 질문]과 [겹치는 이전 질문]을 직접 비교해보면 겹침이 보일 것입니다 — 이미 확인한 실제 파일 내용을 참고해 완전히 다른 각도로 다시 질문하세요. 아래 항목들과도 겹치면 안 됩니다:\n\n`
            + rejected.map((r, i) => `${i + 1}. [생성한 질문] ${r.candidate}\n   [겹치는 이전 질문] ${r.dupOf}`).join("\n"),
        });
      }
    }
  }

  // D197: fixes "grading only sees the single turn the loop happened to stop on" --
  // gradeAnswer() used to take (question, answer) for just `transcript[transcript.length-1]`
  // and score ALL 5 rubric axes off that one exchange. Two concrete failures this caused:
  // (a) reaching REFLECTION already means L1-L3 all failed to defend (see p03-4's own
  //     prompt text) -- a "모르겠습니다" there zeroed out all 5 axes even when L1-L3 had
  //     real partial answers, because those answers were never sent to the grading prompt
  //     at all (only used as context for generating the NEXT question).
  // (b) symmetric and worse: an L1 `defended` breaks the loop after 1 turn, but the same
  //     5-axis prompt still ran against that single L1 exchange -- axes like 반례_대응/
  //     대안_비교/자기_수정 were never even asked about, yet the prompt's own "무관하면
  //     1점" rule could tank them, producing a "소유" verdict badge next to mostly-weak
  //     axis cards on result.html.
  //   WHY: grade the FULL transcript actually run, with each axis restricted to evidence
  //   from its mapped level(s) (prompt_manifest.json's new p03-7.axis_level_map). An axis
  //   is only sent to the LLM if >=1 of its mapped levels is present in `transcript`;
  //   axes with none reached get a fixed, code-authored "not tested" placeholder instead
  //   of asking the LLM to self-report untested-ness (rejected: that's a hallucination risk
  //   on a signal the JS layer already has ground truth for via `transcript`).
  //   COST: prompt is transcript-shaped now instead of single-Q&A (bounded token growth,
  //   max 4 turns). Tool schema's `properties`/`required` is a dynamic subset of axes per
  //   call instead of a fixed 5-key shape -- one more moving part, traded for never asking
  //   the LLM to invent a score for something it wasn't shown.
  //   EXIT: if a future rubric needs an axis scored from >1 disjoint level groups
  //   separately, this per-axis-single-score shape stops being enough (would need
  //   per-(axis,level) sub-scores). Not needed today -- every current axis maps to one
  //   contiguous set of levels. If the axis_level_map turns out wrong for a given axis,
  //   only that JSON entry needs to change, no code here.
  function testedLevelsOf(transcript) {
    return new Set(transcript.map((t) => t.level));
  }

  function gradableAxes(axisLevelMap, axes, testedLevels) {
    return axes.filter((axis) => {
      const levels = axisLevelMap[axis];
      // unmapped axis (e.g. a team override adds one without updating axis_level_map) ->
      // always gradable off the full transcript, never silently dropped to "not tested".
      if (!levels || !levels.length) return true;
      return levels.some((lvl) => testedLevels.has(lvl));
    });
  }

  function buildTranscriptBlock(transcript) {
    return transcript.map((t) => `[${t.level.toUpperCase()}] 질문: ${t.question}\n[${t.level.toUpperCase()}] 학생 답변: ${t.answer}`).join("\n\n");
  }

  function buildAxisGuidanceBlock(axisLevelMap, axes, gradable, testedLevels) {
    const levelsLabel = (axis) => (axisLevelMap[axis] || []).map((l) => l.toUpperCase()).join("/") || "전체";
    let block = `이번 세션에서 진행된 레벨: ${[...testedLevels].map((l) => l.toUpperCase()).join(", ")}.\n`;
    block += `아래 축은 실제로 진행된 레벨의 턴을 근거로 채점 대상입니다:\n${gradable.map((a) => `- ${a}: 근거 턴 = ${levelsLabel(a)}`).join("\n")}`;
    const untested = axes.filter((a) => !gradable.includes(a));
    if (untested.length) {
      block += `\n\n다음 축은 이 세션에서 해당 레벨까지 도달하지 않아 채점 대상에서 제외됩니다 (코드로 고정 처리됨, 응답에 포함하지 마세요): ${untested.join(", ")}`;
    }
    return block;
  }

  function notTestedEvidence(axis, axisLevelMap) {
    const levelsLabel = (axisLevelMap[axis] || []).map((l) => l.toUpperCase()).join("/");
    return `이 세션은 ${levelsLabel} 레벨까지 진행되지 않아 채점하지 않았습니다 (조기 방어 성공했거나, 세션이 그 전에 종료됨).`;
  }

  // Change #1: takes `model` explicitly (was module-level selectedModel fallback).
  // D197: takes the full `transcript` (all turns actually run) instead of a single
  // (question, answer) pair -- see the D197 comment above for the full WHY/COST/EXIT.
  async function gradeAnswer(finding, transcript, maxAttempts, model) {
    const stage = LabApp.getStage("p03", "p03-7");
    const override = LabApp.getOverride("p03", "p03-7") || {};
    const rubric = override.rubric || stage.rubric;
    const rubricOverridden = Boolean(override.rubric_overridden);
    const axisLevelMap = override.axis_level_map || stage.axis_level_map || {};
    const axes = Object.keys(rubric);
    const testedLevels = testedLevelsOf(transcript);
    const gradable = gradableAxes(axisLevelMap, axes, testedLevels);

    let rubricBlock = "";
    for (const axis of gradable) {
      const levels = rubric[axis];
      rubricBlock += `### ${axis} (근거 턴: ${(axisLevelMap[axis] || []).map((l) => l.toUpperCase()).join("/") || "전체"})\n`;
      for (const score of ["5", "4", "3", "2", "1"]) rubricBlock += `  ${score}점: ${levels[score]}\n`;
    }
    const userMsg = LabApp.fillTemplate(LabApp.resolveTemplate("p03", "p03-7", "user_template"), {
      rubric_block: rubricBlock,
      axis_guidance_block: buildAxisGuidanceBlock(axisLevelMap, axes, gradable, testedLevels),
      finding_text: finding.finding || "", finding_file: finding.file || "",
      transcript_block: buildTranscriptBlock(transcript),
    });
    const tool = {
      name: "grade_interview_answer",
      description: "학생 답변을 FR-04-01 5축 루브릭으로 채점한다 (이번 세션에서 실제로 진행된 레벨에 해당하는 축만).",
      input_schema: { type: "object", properties: Object.fromEntries(gradable.map((a) => [a, { type: "object", properties: { score: { type: "integer" }, evidence: { type: "string" } }, required: ["score", "evidence"] }])), required: gradable },
    };
    // D182: p03-7's manifest `model` param was removed so this falls through to the
    // top-level toggle -- previously the manifest's fixed default would have won here
    // every time via resolveParam's precedence, making a toggle pointless.
    const resolvedModel = LabApp.resolveParam("p03", "p03-7", "model") || model;
    const llmGrades = gradable.length
      ? await LabLLM.chatTool({ model: resolvedModel, messages: [{ role: "user", content: userMsg }], tool, maxTokens: 2048, maxAttempts })
      : {};
    // D197: JS is the sole source of truth for axis eligibility -- merge LLM-scored
    // (gradable) axes with deterministic not-tested placeholders for the rest.
    const grades = {};
    for (const axis of axes) {
      grades[axis] = gradable.includes(axis)
        ? { ...llmGrades[axis], tested: true }
        : { score: null, evidence: notTestedEvidence(axis, axisLevelMap), tested: false };
    }
    return { grades, rubric_overridden: rubricOverridden };
  }

  // Change #1/#2/#3/#4/#6/#7/#8 -- see file header for the full mapping. Turn-loop
  // sequencing itself (the verdict sentinel defaulting to "exhausted_at_cap" and only
  // flipping on an in-loop break) is untouched. D199 removed the old prevClassification
  // hand-off to generateQuestion() -- transcript already carries every turn's
  // classification, so that separate variable was redundant (see generateQuestion()'s
  // D199 comment).
  //
  // input: { finding, codeContexts, model }
  // hooks: { onStatus(text,kind), onProgress(msg), onRunStart(), onRunEnd(elapsedMs),
  //          onQuestion({level,question,turnIndex,totalTurns}),
  //          getAnswer({level,question}) -> Promise<string>,
  //          onAnswerRecorded({level,question,answer,classification}),
  //          countdown: {start(totalMinutes), resume(), pause(), stop()} }
  async function run(input, hooks) {
    // D200: repoRef ({owner,repo,branch} or null for ZIP uploads) enables the live
    // fact-check tool-loop in generateQuestion() -- see its D200 comment.
    const { finding, codeContexts, model, repoRef } = input;
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
    // D193: declared *outside* the try block on purpose -- the catch block below needs to
    // read this too (to finish the same run row on failure), and a `let` declared inside
    // try is out of scope inside its own catch.
    let dbRun = null;
    // D196 (2026-07-17): reassigned once the abandon guard is armed inside the try below.
    // Declared out here for the same reason as dbRun -- the catch block must be able to
    // disarm it before writing the run's real "error" status.
    let disarmAbandon = () => {};
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
      // D205: `repoRef` missing (ZIP upload, or a session scanned before D200 shipped) used
      // to silently skip the whole fact-check mechanism with zero visible trace -- not even
      // a console.log. Surfaced once per run, not per turn, via the same `⚠`-prefixed
      // onProgress convention D199 already uses to promote console-only warnings into a
      // trainee-visible chat bubble (session.html's existing `msg.startsWith("⚠")` handler
      // needs no change). No message when repoRef IS present -- this is a "tell me when a
      // capability is silently absent" warning, not routine noise on the normal path.
      if (!(repoRef && repoRef.owner && repoRef.repo)) {
        hooks.onProgress("⚠ 저장소 식별 정보 없음(ZIP 업로드이거나 이전 버전에서 스캔한 세션) — 이번 인터뷰는 실시간 코드 재확인 없이 저장된 스니펫만으로 진행됩니다");
      }

      const transcript = [];
      let verdict = "exhausted_at_cap";
      const maxTurns = LabApp.resolveParam("p03", "p03-6", "max_turns") || 4;
      const totalTurns = Math.min(LEVELS.length, maxTurns);

      // D193 (2026-07-16): open the DB-side run row *before* the turn loop instead of
      // only at the very end -- see db.js's startRun()/logTurn() comment for the full
      // WHY/COST/EXIT. Failure here is non-fatal to the actual verification flow (same
      // "DB 미설정" tone as maybeSaveRun below) -- a team member who can't reach Supabase
      // for a moment should still be able to answer questions, just without persistence.
      if (LabDB.isConfigured()) {
        try {
          dbRun = await LabDB.startRun({ pipeline: "p03", model, input_meta: { finding_id: finding.id }, overrides: {} });
        } catch (e) {
          hooks.onProgress(`DB 세션 시작 실패(턴별 저장 없이 진행): ${e.message}`);
        }
      }

      // D196 (2026-07-17): while the trainee is still answering, a tab close should finalize
      // this run as "abandoned" instead of leaving it stranded at "running" (see db.js
      // armAbandonBeacon for the full WHY/COST/EXIT). Scoped to `pagehide` ONLY -- not
      // visibilitychange -- because switching tabs to look something up must not count as
      // abandonment. Disarmed the instant we begin finalizing (loop done, or error) so it can
      // never race or overwrite the real done/error status written below.
      if (dbRun) {
        try {
          const sendAbandon = await LabDB.armAbandonBeacon(dbRun.id);
          const onPageHide = () => { sendAbandon(); };
          window.addEventListener("pagehide", onPageHide);
          disarmAbandon = () => { window.removeEventListener("pagehide", onPageHide); };
        } catch (e) {
          hooks.onProgress(`이탈 감지 설정 실패(턴 저장은 정상 동작): ${e.message}`);
        }
      }

      for (let i = 0; i < totalTurns; i++) {
        const level = LEVELS[i];
        const genAttemptInfo = await resolveMaxAttempts();
        if (genAttemptInfo.elevated) hooks.onProgress(`⚠ 현재 트래픽 ${genAttemptInfo.count}/${genAttemptInfo.threshold}${genAttemptInfo.scopeNote} -- 재시도 여유를 ${ELEVATED_MAX_ATTEMPTS}회로 늘려서 요청`);
        hooks.onProgress(`${level.toUpperCase()} 질문 생성 중...`);
        // D199: transcript already carries every turn's classification -- generateQuestion()
        // derives the cumulative verdict trail from it directly, no separate param needed.
        // D200: repoRef enables live fact-checking for L2/L3/Reflection (null/no-op for ZIP).
        const question = await generateQuestion(level, finding, codeContext, transcript, genAttemptInfo.maxAttempts, model, hooks.onProgress, repoRef);
        hooks.onQuestion({ level, question, turnIndex: i, totalTurns });
        hooks.onProgress("답변 대기 중...");
        hooks.countdown.resume(); // D182: only start ticking once the human can actually see+answer this question
        const answer = await hooks.getAnswer({ level, question });
        hooks.countdown.pause();
        hooks.onProgress("답변 분류 중 (결정론적 분류기, LLM 아님)...");
        const classification = await classifyAnswer(category, answer, level);
        transcript.push({ level, question, answer, classification });
        hooks.onAnswerRecorded({ level, question, answer, classification });
        // D193: persist this turn immediately -- if a later turn fails, this one still
        // survives in stage_events (see p03_progress_view). Failure here must not break
        // the actual verification flow: the answer was already recorded in-memory above.
        if (dbRun) {
          try {
            await LabDB.logTurn({ run_id: dbRun.id, stage_id: level, seq: i, output: { level, question, answer, classification } });
          } catch (e) {
            hooks.onProgress(`턴 저장 실패(진행은 계속됨): ${e.message}`);
          }
        }
        if (classification.verdict === "defended") { verdict = "defended"; break; }
      }

      // D196: answering is complete -- from here the run finalizes (grading -> done). Stop
      // treating a tab close as abandonment; closing during the brief grading window reverts
      // to the prior "row stays running" behavior, which is fine since the trainee already
      // finished every answer. maybeSaveRun() below writes the real "done" status.
      disarmAbandon();
      hooks.onProgress("5축 채점 중...");
      const gradeAttemptInfo = await resolveMaxAttempts();
      if (gradeAttemptInfo.elevated) hooks.onProgress(`⚠ 현재 트래픽 ${gradeAttemptInfo.count}/${gradeAttemptInfo.threshold}${gradeAttemptInfo.scopeNote} -- 재시도 여유를 ${ELEVATED_MAX_ATTEMPTS}회로 늘려서 요청`);
      // D197: grade the full transcript (all turns actually run), not just the last turn --
      // see gradeAnswer()'s D197 comment for the full WHY/COST/EXIT.
      const { grades, rubric_overridden } = await gradeAnswer(finding, transcript, gradeAttemptInfo.maxAttempts, model);

      const finishedAt = new Date();
      hooks.onRunEnd(finishedAt - startedAt);
      hooks.countdown.stop();
      hooks.onStatus("완료", "done");
      const result = { finding, verdict, turns: transcript.length, transcript, grades, rubric_overridden };
      await maybeSaveRun(result, startedAt, finishedAt, model, hooks, dbRun);
      return result;
    } catch (err) {
      hooks.onRunEnd(new Date() - startedAt);
      hooks.countdown.stop();
      console.error(err);
      hooks.onStatus(`오류: ${err.message}`, "error");
      hooks.onProgress(`오류: ${err.message}`);
      // D196: this run is finalizing as "error" -- disarm the abandon guard first so a
      // pagehide during error handling can't overwrite that with "abandoned".
      disarmAbandon();
      // D193: if a DB run row was already opened, finish (UPDATE) that same row instead
      // of LabApp.saveFailedRun()'s fresh insert-with-artifacts:[] -- the whole point is
      // that any turns already logged via logTurn() above must survive this failure.
      // saveFailedRun() stays the fallback for the case dbRun was never obtained (DB not
      // configured, or startRun() itself failed) -- unchanged behavior for that case, and
      // unchanged entirely for P01/P02 which don't go through this function at all.
      if (dbRun) {
        try {
          await LabDB.saveRun({ run_id: dbRun.id, status: "error", error: String((err && err.message) || err), finished_at: new Date().toISOString(), artifacts: [] });
        } catch (saveErr) {
          hooks.onProgress(`실패 기록 저장도 실패: ${saveErr.message}`);
        }
      } else {
        await LabApp.saveFailedRun("p03", model, err, startedAt, hooks.onProgress);
      }
      throw err;
    }
  }

  // Change #1: `model` param replaces the original's module-level selectedModel read.
  // D193: `dbRun` param added (optional) -- when the caller already opened a run row via
  // startRun(), pass its id through so saveRun() UPDATEs that same row instead of
  // inserting a second one for the same session.
  async function maybeSaveRun(result, startedAt, finishedAt, model, hooks, dbRun) {
    if (!LabDB.isConfigured()) {
      hooks.onProgress("Supabase 미설정 — 결과는 화면에만 표시됨");
      return;
    }
    try {
      await LabDB.saveRun({
        run_id: dbRun ? dbRun.id : undefined,
        pipeline: "p03",
        model,
        input_meta: { finding_id: result.finding.id },
        overrides: {},
        rubric_overridden: result.rubric_overridden,
        artifacts: [{ kind: "transcript", content: result.transcript }, { kind: "grades", content: result.grades }],
        started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
        status: "done",
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
