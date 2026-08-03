// P04 전체 오케스트레이션. 이 파일은 세션에 걸쳐 커진다 -- 2단계(코드 분석)는 이 커밋에서,
// 3단계(문답 루프)·4단계(보고서)는 후속 커밋에서 채운다. 페이지(analysis.html 등)는 DOM을
// 직접 만지지 않고 이 파일이 노출하는 hooks 콜백만 받는다 -- P02Engine/구 P03Engine과 같은
// "순수 엔진 + hooks" 분리를 그대로 따른다.
const POCEngine = (() => {
  /**
   * 1단계에서 저장된 setup으로부터 files를 다시 확보한다.
   * D-poc5: files 본문은 세션 저장소를 거치지 않는다 -- ZIP은 IndexedDB(session-state.js),
   * GitHub는 repoRef만 들고 있다가 매번 다시 가져온다(D200/D210이 P03에서 확립한 패턴과 동일).
   */
  async function resolveFiles(setup, hooks) {
    if (setup.submission.method === "zip") {
      const zipFiles = await SessionState.loadZipFileMap();
      if (!zipFiles) throw new Error("ZIP 파일 캐시를 찾을 수 없습니다 -- 1단계에서 다시 업로드하세요.");
      return { scanInput: { method: "zip", zipFiles } };
    }
    return {
      scanInput: {
        method: "pat",
        repoInput: setup.submission.repoInput,
        branch: setup.submission.branch,
      },
    };
  }

  /**
   * 실제 파일 내용 맵을 다시 확보한다(재스캔은 하지 않는다) -- session.html의 코드 패널처럼
   * 원문(하이라이트용 전체 파일)이 필요하지만 Pyodide 구조 스캔은 불필요한 경우 전용.
   * resolveFiles()는 P02Engine.run() 앞단(스캔용) 입력만 만드는 반면, 이건 그 자체로 files를
   * 반환한다 -- session.html은 pyodide.js/jszip.min.js 스크립트 태그 없이도 이걸 쓸 수 있다
   * (ZIP 경로는 이미 파싱된 IndexedDB 캐시를 읽기만 하고, GitHub 경로는 fetch만 한다).
   */
  async function resolveFileContents(setup, onProgress) {
    if (setup.submission.method === "zip") {
      const zipFiles = await SessionState.loadZipFileMap();
      if (!zipFiles) throw new Error("ZIP 파일 캐시를 찾을 수 없습니다 -- 1단계에서 다시 업로드하세요.");
      return zipFiles;
    }
    const { owner, repo } = P02Engine.parseRepoInput(setup.submission.repoInput);
    const pat = LabConfig.get("github-pat");
    const fetched = await P02Engine.fetchGithubRepo(owner, repo, setup.submission.branch || null, pat, onProgress || (() => {}));
    return fetched.files;
  }

  /**
   * 분석 문서를 다른 스테이지의 프롬프트에 넣을 때 쓰는 형태. deep_dive(p04-1b 산출물)를
   * 떼어낸다.
   *
   * D-poc13 (2026-08-03): p04-3(문제 선정)과 p04-6(보고서)은 analysisDoc을 통째로
   * JSON.stringify한 뒤 앞에서부터 8000/6000자로 자른다. 4단계 fan-out이 붙인 deep_dive는
   * decision_point 하나당 수백~천 자라, 그대로 두면 뒤쪽 decision_points가 **잘려서 사라진다** --
   * 심층 분석을 켠 대가로 문제 선정이 보는 후보가 줄어드는 조용한 회귀다.
   *   WHY 그냥 truncation 예산을 늘리지 않는가: 그건 두 스테이지의 입력 크기와 비용을
   *   같이 늘리는 결정이고, 여기서 필요한 건 "새 기능이 기존 입력을 밀어내지 않는 것"뿐이다.
   *   이 함수를 거치면 두 스테이지가 보는 문자열은 fan-out 이전과 **바이트 단위로 동일**하다.
   *   COST: p04-3/p04-6은 심층 분석 내용을 못 본다 -- 더 나은 문제를 고를 수도 있었을 정보를
   *   안 주는 셈이다. 그 이득은 미측정이고, 잘림으로 인한 손실은 확실하다. 확실한 손실부터 막는다.
   *   EXIT: 심층 분석을 문제 선정에 반영하고 싶어지면, deep_dive를 통째로 넘기는 대신
   *   decision_point마다 한 줄로 요약해 넣고(예산 통제) 그때 truncation 값을 재산정한다.
   *   deep_dive는 analysisDoc 자체에는 그대로 남아 화면·저장·보고서 렌더에 쓰인다.
   */
  function analysisDocForPrompt(analysisDoc) {
    if (!analysisDoc || !Array.isArray(analysisDoc.decision_points)) return analysisDoc;
    return {
      ...analysisDoc,
      decision_points: analysisDoc.decision_points.map(({ deep_dive, ...rest }) => rest),
    };
  }

  function findingsBlockFor(findings) {
    const text = JSON.stringify(findings, null, 0);
    return text.length > 6000 ? text.slice(0, 6000) + `\n...(총 ${findings.length}건 중 일부만 표시)` : text;
  }

  /**
   * 2단계: 코드 분석 -> {분석 문서, P/F 결과, 문제 3개, 문제별 L1~L4+힌트}.
   * @param {object} setup   POCState.loadSetup()의 결과
   * @param {object} hooks   {onProgress(msg), onStatus(text,kind)}
   * @returns {Promise<object>} analysis 페이로드 (POCState.saveAnalysis에 그대로 넣을 형태)
   */
  async function runAnalysisStage(setup, hooks) {
    const startedAt = new Date();
    const model = setup.model;
    hooks.onStatus("코드 가져오는 중...", "running");

    const { scanInput } = await resolveFiles(setup, hooks);

    // P02Engine.run()이 fetch + Pyodide 스캔 + (best-effort) DB 저장을 전부 한다 -- 이 PoC는
    // 그 결과(files, findings, repoRef)만 가져다 쓴다. 스캔 로직 자체를 다시 구현하지 않는다.
    const { result: p02Result, files, repoRef } = await P02Engine.run(scanInput, {
      onStatus: () => {},
      onRunStart: () => {},
      onProgress: (m) => hooks.onProgress(`[코드 스캔] ${m}`),
      onRunEnd: () => {},
    });
    const findings = (p02Result.judgment && p02Result.judgment.findings) || [];
    const fileCount = Object.keys(files).length;
    hooks.onProgress(`코드 스캔 완료 -- 파일 ${fileCount}개, finding ${findings.length}건`);

    const teachesBlock = TeachesSource.formatTeachesBlock(setup.teaches);
    const findingsBlock = findingsBlockFor(findings);

    // ── p04-1: 분석 문서 ────────────────────────────────────────────────────
    // D-poc13 (2026-08-03, 1단계 배선 완료): buildCodeBlock이 이제 fan_in 내림차순으로
    // 12,000자 예산을 채운다 -- 알파벳으로 늦은 핵심 파일이 알파벳으로 이른 사소한 파일에
    // 밀려 잘리던 문제(D-poc13 원 설계 문서 참고)를 새 LLM 호출 0개로 해결한다. fan_in이
    // 없으면(Swift 등 구조 스캔 불가, orderFilesByImportance 자체 안전장치) 알파벳순으로
    // 안전하게 떨어진다 -- 동작이 오늘과 완전히 같아지는 폴백이지 에러 경로가 아니다.
    //   COST: fan_in은 basename 키라 동명 파일이 합산된다(cognition/two_tier_scan.py의 같은
    //   경고) -- 정렬 순서만 바뀌고 포함 여부 판정 로직(buildCodeBlock 자체)은 그대로라
    //   최악이라도 "오늘과 다른 순서로 같은 예산을 채움"이다.
    //   EXIT: fan_in 순서가 알파벳순보다 나쁘다고 관측되면 order 인자 이 한 줄만 지운다.
    hooks.onProgress("코드 분석 문서 작성 중...");
    const docStage = POCStage.getStage("p04-1");
    const fanIn = (p02Result.scan && p02Result.scan.tier_a_structural || {}).fan_in;
    const docCodeBlock = CodeFragment.buildCodeBlock(files, {
      maxChars: docStage.truncation.code_block,
      order: CodeCandidates.orderFilesByImportance(files, fanIn),
    });
    const analysisDoc = await POCStage.call("p04-1", {
      teaches_block: teachesBlock, findings_block: findingsBlock, code_block: docCodeBlock,
    }, { model, onProgress: hooks.onProgress });

    // ── p04-1b: 상위 K개 위치 심층 분석 (1~4단계 fan-out) ────────────────────
    // D-poc13 (2026-08-03, 4단계 배선 완료): p04-1은 코드베이스 전체를 예산 안에서 한 번
    // 얕게 본다. 그 짝으로, 후보 식별 -> grounding -> 랭킹을 거쳐 고른 상위 K개(K<=5) 위치
    // 각각을 p04-1b가 병렬로 한 곳씩 깊게 본다. 세 단계 전부 app/code-candidates.js에 있고
    // 여기서는 runFanout() 한 번만 부른다(그 파일 §4 설계 규칙 그대로).
    //   WHY try/catch로 통째로 감싸는가: 이 블록은 **부가 정보**다. 후보 수집·랭킹·트래픽
    //   조회 중 어디가 예상 밖으로 터지더라도 2단계 전체(요구사항 P/F, 문제 선정, 질문 생성)를
    //   같이 죽여서는 안 된다. 개별 p04-1b 실패는 이미 runFanout 안에서 정상 경로로 처리되고
    //   (allSettled), 여기 catch는 그 바깥의 예상 밖 예외만 받는다.
    //   COST: 성공 시 제출물당 LLM 호출이 최대 K개(=3, 상한 5) 늘어난다. 워커가 제출 1건당
    //   최대 3회 재시도하므로 실제 NVIDIA 요청은 최대 3K회이고, 그래서 runFanout이 호출 직전
    //   현재 rpm을 보고 K를 깎는다(재시도를 늘리는 게 아니라 K를 줄이는 방향 -- P03 D181과
    //   반대인 이유는 code-candidates.js 4단계 설계 규칙 3에 있다).
    //   신뢰성 계약: 실패하거나 K=0으로 깎이면 decision_points에 deep_dive 키가 아예 안 붙고,
    //   화면은 오늘과 정확히 같다(근거 코드 파편 + why_it_matters 한 줄).
    let fanout = null;
    try {
      fanout = await CodeCandidates.runFanout({
        analysisDoc, files, findings, fanIn,
        teachIds: setup.teaches.map((t) => t.id),
        model, k: 3, onProgress: hooks.onProgress,
      });
      if (fanout) analysisDoc.decision_points = fanout.decision_points;
    } catch (e) {
      hooks.onProgress(`⚠ 핵심 위치 심층 분석 단계를 건너뜁니다(분석 문서는 그대로 유지): ${e.message}`);
    }

    // decision_points는 모델이 스스로 지목한 {file,symbol}다(D-poc10 -- 줄 번호는 LLM이
    // 세지 않고 우리가 심볼 문자열로 찾아 산정한다) -- 실제 파일과 대조해 검증하고,
    // 지어낸 위치는 화면에 노출하지 않는다(코드 파편이 곧 근거이므로 여기서 거르지 않으면
    // "존재하지 않는 코드"를 근거라고 보여주게 된다).
    const decisionPoints = (Array.isArray(analysisDoc.decision_points) ? analysisDoc.decision_points : [])
      .map((dp) => ({ ...dp, fragment: CodeFragment.extractFragment(files, { file: dp.file, symbol: dp.symbol }) }));
    const droppedDecisionPoints = decisionPoints.filter((dp) => !dp.fragment.valid).length;
    if (droppedDecisionPoints) {
      hooks.onProgress(`⚠ 분석 문서의 decision_points 중 ${droppedDecisionPoints}건은 실제 파일 범위와 맞지 않아 근거 없음으로 표시됩니다`);
    }

    // ── p04-2: 요구사항 P/F ──────────────────────────────────────────────────
    hooks.onProgress(setup.requirements.length ? "요구사항 P/F 판정 중..." : "요구사항 없음 -- P/F 판정 생략");
    const requirementsResult = await Requirements.judge(setup.requirements, files, { model, onProgress: hooks.onProgress });

    // ── p04-3: 문제 3개 선정 ─────────────────────────────────────────────────
    hooks.onProgress("문제 선정 중...");
    const questionCount = POCScoring.thresholds.questionsPerSubmission;
    const topicsRaw = await POCStage.call("p04-3", {
      teaches_block: teachesBlock,
      analysis_block: JSON.stringify(analysisDocForPrompt(analysisDoc)).slice(0, 8000),
      findings_block: findingsBlock,
      question_count: questionCount,
    }, { model, onProgress: hooks.onProgress });

    let topics = Array.isArray(topicsRaw.topics) ? topicsRaw.topics : [];
    // 검증 1: teach_id가 실제로 선택된 teaches 안에 있어야 하고, 서로 달라야 한다. 어긴 topic은
    // 버린다 -- 존재하지 않는 teach를 참조하는 문제를 만들 수는 없다.
    const teachIds = new Set(setup.teaches.map((t) => t.id));
    const seenTeach = new Set();
    topics = topics.filter((t) => {
      if (!teachIds.has(t.teach_id) || seenTeach.has(t.teach_id)) return false;
      seenTeach.add(t.teach_id);
      return true;
    });
    // 검증 2 (D-poc10): code_ref가 실제 파일에서 위치를 못 잡으면 이 단계에서 걸러낸다 --
    // 안 걸러내면 HintLadder.freezeQuestionSet이 "근거 코드 파편을 확인할 수 없음"인 채로
    // 질문·힌트 생성을 계속 진행해(LLM 호출만 낭비) 결국 세션에서 조용히 깨졌었다.
    // 위치가 확정된 lines를 topic.code_ref에 그대로 되먹여, 이후 단계가 symbol을 다시
    // 찾을 필요 없이 산정된 사실만 쓰게 한다.
    const unresolvedTopics = [];
    topics = topics.filter((t) => {
      const fragment = CodeFragment.extractFragment(files, t.code_ref);
      if (!fragment.valid) { unresolvedTopics.push({ title: t.title, reason: fragment.reason }); return false; }
      t.code_ref = { file: fragment.file, lines: fragment.lines };
      return true;
    }).slice(0, questionCount);
    if (unresolvedTopics.length) {
      hooks.onProgress(`⚠ 문제 ${unresolvedTopics.length}건은 코드 위치를 찾지 못해 제외됨: ${unresolvedTopics.map((u) => `"${u.title}"(${u.reason})`).join(", ")}`);
    }
    if (topics.length < questionCount) {
      hooks.onProgress(`⚠ 문제 ${questionCount}개를 요청했으나 유효한 문제 ${topics.length}개만 확보됨 (teach/코드위치 검증 실패분 제외)`);
    }

    // ── p04-4: 문제별 L1~L4 (+ hintMode==="frozen"이면 힌트도 이 단계에서 동결) ──────
    const teachesById = new Map(setup.teaches.map((t) => [t.id, t]));
    const hintMode = setup.hintMode || POCScoring.hintMode.default;
    const questionSets = [];
    for (const topic of topics) {
      hooks.onProgress(`"${topic.title}" 질문 생성 중...`);
      const qs = await HintLadder.freezeQuestionSet(topic, {
        teach: teachesById.get(topic.teach_id), files, model, onProgress: hooks.onProgress, hintMode,
      });
      if (qs.flagged) hooks.onProgress(`⚠ "${topic.title}" 질문이 선택지 금지 규칙을 계속 위반해 flagged 상태로 남음 -- 검토 필요`);
      questionSets.push(qs);
    }

    // D8: frozen/adaptive 소요시간 비교용 -- 질문 생성은 항상, 힌트 사전생성은 frozen일
    // 때만 값이 있다(adaptive는 세션 단계에서 발생하므로 여기선 빈 배열).
    const timing = {
      hintMode,
      questionGenMs: questionSets.map((qs) => qs.questionGenMs || 0),
      frozenHintGenMs: questionSets.flatMap((qs) => qs.frozenHintTimings || []),
    };

    const finishedAt = new Date();
    const analysis = {
      analysisDoc, decisionPoints, requirementsResult, topics, questionSets,
      findings, fileCount, repoRef: repoRef || null,
      submissionMethod: setup.submission.method,
      model, hintMode, timing,
      started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
    };

    hooks.onProgress(`결과가 팀 DB에 저장됨(best-effort)`);
    await saveAnalysisRun(setup, analysis, hooks.onProgress).catch((e) => hooks.onProgress(`DB 저장 실패(결과는 화면에 남아있음): ${e.message}`));

    hooks.onStatus("완료", "done");
    return analysis;
  }

  // D8 (2026-07-30): hint_mode/timing_ms는 vendored shared/db.js가 모르는 컬럼이다 --
  // startRun()/saveRun()은 고정된 필드 집합만 INSERT/UPDATE한다(그 파일은 드리프트
  // 검사 대상이라 시그니처를 못 늘림). 대신 이 함수가 같은 프로젝트에 직접 REST PATCH를
  // 보내 그 run 행에 두 컬럼만 채운다.
  //   WHY 진짜 컬럼인가(JSONB input_meta 필드가 아니라): "DB에 칼럼 구별 지어서" 요구를
  //   문자 그대로 만족시키려면 실제 컬럼이 필요하다 -- input_meta에 욱여넣으면 조회할 때
  //   매번 ->>'hint_mode' 캐스팅이 필요해 "구별"의 편의가 떨어진다.
  //   COST: RLS의 "update own"(member_id = auth.uid())을 통과하려면 anon key가 아니라
  //   로그인한 사용자의 실제 세션 access_token이 필요하다 -- 미로그인이면 0행 매칭으로
  //   조용히 실패한다(기존 관용과 동일하게 non-fatal, best-effort).
  //   EXIT: db/migrations/p04_timing_schema.sql을 되돌리면(컬럼 DROP) 이 함수는 계속 호출은 되지만
  //   PATCH가 컬럼없음 에러로 실패할 뿐 메인 저장 흐름에는 영향 없다(catch로 감싸져 있음).
  async function patchTimingColumns(runId, patch) {
    if (!runId) return;
    const client = await LabDB.ensureClient();
    const { data } = await client.auth.getSession();
    const token = (data && data.session && data.session.access_token) || LabConfig.get("supabase-anon-key");
    const url = `${LabConfig.get("supabase-url")}/rest/v1/runs?id=eq.${encodeURIComponent(runId)}`;
    const res = await fetch(url, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "apikey": LabConfig.get("supabase-anon-key"),
        "Authorization": `Bearer ${token}`,
        "Prefer": "return=minimal",
      },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`timing 컬럼 PATCH 실패 (HTTP ${res.status}): ${text.slice(0, 200)}`);
    }
  }

  // best-effort DB 기록. db/migrations/p04_schema.sql이 아직 적용되지 않았으면 CHECK 제약(23514)에
  // 걸려 실패하는데, 그건 이 함수가 아니라 그 마이그레이션의 책임이다 -- 여기서는 실패해도
  // 화면 흐름을 막지 않는다(이 저장소의 saveFailedRun/maybeSaveRun과 동일한 관용).
  async function saveAnalysisRun(setup, analysis, onProgress) {
    if (!LabDB.isConfigured()) return;
    const run = await LabDB.startRun({
      pipeline: "p04",
      model: analysis.model,
      input_meta: {
        teach_ids: setup.teaches.map((t) => t.id),
        requirement_count: setup.requirements.length,
        file_count: analysis.fileCount,
        submission_method: analysis.submissionMethod,
      },
      overrides: {},
    });
    try {
      await patchTimingColumns(run.id, { hint_mode: analysis.hintMode, timing_ms: analysis.timing });
    } catch (e) {
      if (onProgress) onProgress(`⚠ 소요시간 컬럼 저장 실패(본 결과 저장은 성공): ${e.message}`);
    }
  }

  // ── 3단계: 문답 ──────────────────────────────────────────────────────────────

  /** p04-5 채점 호출 1회. */
  async function gradeLevel({ axis, question, hintsUsed, hintText, answer, codeBlock, codeRefStr, model, onProgress }) {
    const data = await POCStage.call("p04-5", {
      rubric_block: POCScoring.buildRubricBlock(axis),
      question,
      hints_used: hintsUsed,
      hints_block: hintText ? `힌트 ${hintsUsed}: ${hintText}` : "(힌트 없음 -- 자력 답변)",
      code_block: codeBlock,
      code_ref: codeRefStr,
      answer,
    }, { model, onProgress });
    const score = Math.max(0, Math.min(5, Number(data.score)));
    return { score, matched_level: data.matched_level || "", evidence: data.evidence || "", missing: data.missing || "" };
  }

  /**
   * 3단계: 문제 순서대로 L1~L4를 진행한다. 레벨 실패(힌트 2회 소진 후 미달)는 해당 문제를
   * 즉시 끝내고 다음 문제의 L1로 넘어간다 (D3의 onLevelFail="endQuestion").
   *
   * @param {object} setup     1단계 산출물(POCState.loadSetup()) -- teaches 조회용
   * @param {object} analysis  2단계 산출물(POCState.loadAnalysis())
   * @param {object} hooks     {
   *   onProgress(msg), onStatus(text,kind),
   *   onTopicStart({index,topic}), onTopicEnd({index,outcome,failedAxis}),
   *   onHintPending({topicIndex,axis,hintLevel}), onLevelResult({topicIndex,axis,hintsUsed,score,passed}),
   *   getAnswer({topicIndex,topic,axis,question,hintsUsed,hintText,codeRef,codeBlock}) -> Promise<string>
   * }
   * @returns {Promise<object>} session 페이로드
   */
  async function runSessionStage(setup, analysis, hooks) {
    const model = analysis.model;
    const results = [];
    const teachesById = new Map((setup.teaches || []).map((t) => [t.id, t]));
    const adaptiveHintGenMs = []; // D8: adaptive 모드에서만 채워짐(frozen은 세션 중 LLM 호출 없음)

    // db.js의 startRun()/logTurn() 관용(D193)과 동일: 턴이 실제로 일어나기 전에 run 행을
    // 먼저 열어야 각 턴을 즉시 기록할 수 있다 -- 세션 끝에서야 한 번에 저장하면 중간에
    // 브라우저가 닫혔을 때 이미 답한 턴까지 통째로 사라진다.
    if (LabDB.isConfigured()) {
      try {
        sessionDbRun = await LabDB.startRun({
          pipeline: "p04", model,
          input_meta: { topic_count: analysis.questionSets.length },
          overrides: {},
        });
      } catch (e) {
        hooks.onProgress(`DB 세션 시작 실패(턴별 저장 없이 진행): ${e.message}`);
      }
    }

    for (let ti = 0; ti < analysis.questionSets.length; ti++) {
      const qs = analysis.questionSets[ti];
      hooks.onTopicStart({ index: ti, topic: qs.topic });

      if (qs.flagged) {
        hooks.onProgress(`"${qs.topic.title}"는 질문 생성이 flagged 상태라 이 세션에서 제외됩니다.`);
        results.push({ topic: qs.topic, levels: [], outcome: "flagged_skipped", failedAxis: null });
        hooks.onTopicEnd({ index: ti, outcome: "flagged_skipped", failedAxis: null });
        continue;
      }

      const codeRefStr = CodeFragment.formatRef(qs.code_ref);
      const levels = [];
      let failedAxis = null;

      const teach = teachesById.get(qs.topic.teach_id);

      for (const lvl of qs.levels) { // HintLadder.normalizeLevels가 이미 L1~L4 순서로 정렬해둠
        const attempts = [];
        let hintsUsed = 0;
        let hintText = null; // 다음 질문과 함께 보여줄 힌트 -- 첫 시도는 없음(자력)
        let hintMs = null;   // D8: 그 힌트를 얻기까지 걸린 시간(frozen=사전생성 시점 값, adaptive=방금 생성한 값)
        let cappedScore = 0;
        let passed = false;

        for (;;) {
          const answer = await hooks.getAnswer({
            topicIndex: ti, topic: qs.topic, axis: lvl.axis, question: lvl.question,
            hintsUsed, hintText, hintMs, codeRef: qs.code_ref, codeBlock: qs.code_block,
          });
          hooks.onProgress(`${qs.topic.title} · ${POCScoring.AXES[lvl.axis].label} 채점 중...`);
          const graded = await gradeLevel({
            axis: lvl.axis, question: lvl.question, hintsUsed, hintText, answer,
            codeBlock: qs.code_block, codeRefStr, model, onProgress: hooks.onProgress,
          });
          const cap = POCScoring.applyCap(graded.score, hintsUsed);
          cappedScore = cap.capped;
          attempts.push({
            hintsUsed, hint: hintText, hintMs, question: lvl.question, answer,
            rawScore: cap.raw, cappedScore: cap.capped, capApplied: cap.capApplied,
            evidence: graded.evidence, missing: graded.missing,
          });
          await logSessionTurn(analysis, { topicIndex: ti, axis: lvl.axis, hintsUsed, question: lvl.question, answer, score: cap.capped });

          passed = POCScoring.passed(cappedScore);
          hooks.onLevelResult({ topicIndex: ti, axis: lvl.axis, hintsUsed, score: cappedScore, passed });
          if (passed) break;
          if (hintsUsed >= POCScoring.thresholds.maxHintsPerLevel) break; // 힌트 소진, 미달 -- 아래에서 처리

          hintsUsed++;
          // D7: hintMode==="frozen"이면 힌트가 이미 2단계법 freezeQuestionSet()에서
          // 생성돼 lvl.hints에 있다 -- LLM 호출 없이 그대로 읽는다(팀 계약: 턴당 채점
          // 호출 1개만). "adaptive"면 D4 개정대로 오답이 확정된 지금 방금 답변을 근거로
          // 힌트를 즉석 생성한다. 근거/트레이드오프는 scoring-config.js의 hintMode 주석.
          if (Array.isArray(lvl.hints)) {
            const frozen = lvl.hints.find((h) => h.lv === hintsUsed);
            hintText = frozen ? frozen.text : HintLadder.fallbackHint(hintsUsed, qs.code_ref);
            hintMs = frozen ? frozen.ms : null;
          } else {
            if (hooks.onHintPending) hooks.onHintPending({ topicIndex: ti, axis: lvl.axis, hintLevel: hintsUsed });
            const hint = await HintLadder.generateHint({
              axis: lvl.axis, hintLevel: hintsUsed, question: lvl.question, attempts,
              teach, codeBlock: qs.code_block, codeRef: qs.code_ref, model, onProgress: hooks.onProgress,
            });
            hintText = hint.text;
            hintMs = hint.ms;
            adaptiveHintGenMs.push({ topicIndex: ti, axis: lvl.axis, lv: hintsUsed, ms: hint.ms });
            if (hooks.onHintTiming) hooks.onHintTiming({ topicIndex: ti, axis: lvl.axis, hintLevel: hintsUsed, ms: hint.ms });
          }
        }

        levels.push({
          axis: lvl.axis, attempts, finalScore: cappedScore, passed,
          hintsUsedFinal: hintsUsed, autonomy: POCScoring.autonomyFor(hintsUsed).label,
        });
        if (!passed) { failedAxis = lvl.axis; break; } // D3 onLevelFail -- 남은 레벨은 시도하지 않음(X)
      }

      const outcome = failedAxis ? `failed_at:${failedAxis}` : "completed";
      results.push({ topic: qs.topic, levels, outcome, failedAxis });
      hooks.onTopicEnd({ index: ti, outcome, failedAxis });
    }

    const timing = { hintMode: analysis.hintMode, adaptiveHintGenMs }; // D8
    const session = { results, model, hintMode: analysis.hintMode, timing, started_at: new Date().toISOString(), finished_at: new Date().toISOString() };
    await saveSessionRun(analysis, session, hooks.onProgress).catch((e) => hooks.onProgress(`DB 저장 실패(결과는 화면에 남아있음): ${e.message}`));
    return session;
  }

  let sessionDbRun = null;
  async function saveSessionRun(analysis, session, onProgress) {
    if (!LabDB.isConfigured() || !sessionDbRun) return; // 세션 시작조차 실패했으면 마무리 저장도 생략
    await LabDB.saveRun({
      run_id: sessionDbRun.id, pipeline: "p04", model: session.model,
      input_meta: { topic_count: analysis.questionSets.length },
      overrides: {}, rubric_overridden: false,
      artifacts: [{ kind: "session_results", content: session.results }],
      started_at: session.started_at, finished_at: session.finished_at, status: "done",
    });
    try {
      await patchTimingColumns(sessionDbRun.id, { hint_mode: session.hintMode, timing_ms: session.timing });
    } catch (e) {
      if (onProgress) onProgress(`⚠ 소요시간 컬럼 저장 실패(본 결과 저장은 성공): ${e.message}`);
    }
  }

  async function logSessionTurn(analysis, turn) {
    if (!LabDB.isConfigured() || !sessionDbRun) return;
    try {
      await LabDB.logTurn({ run_id: sessionDbRun.id, stage_id: turn.axis, seq: turn.topicIndex * 10 + turn.hintsUsed, output: turn });
    } catch (_) {
      // best-effort -- 턴 저장 실패는 흐름을 막지 않는다(이 저장소의 기존 관용, D193 참고)
    }
  }

  // ── 4단계: 보고서 ────────────────────────────────────────────────────────────

  function formatTranscriptBlock(session) {
    return session.results.map((r, i) => {
      const lines = [`## 문제 ${i + 1}: ${r.topic.title} (${r.outcome})`];
      if (r.outcome === "flagged_skipped") {
        lines.push("(질문 생성이 선택지 금지 규칙을 계속 위반해 이 세션에서 건너뜀)");
        return lines.join("\n");
      }
      for (const lvl of r.levels) {
        lines.push(`### ${POCScoring.AXES[lvl.axis].label} -- 최종 ${lvl.finalScore}점 (힌트 ${lvl.hintsUsedFinal}개, ${lvl.passed ? "통과" : "미달"})`);
        lvl.attempts.forEach((a, ai) => {
          lines.push(`시도${ai + 1} (힌트 ${a.hintsUsed}개${a.hint ? ": " + a.hint : ""}): 질문="${a.question}" / 답변="${a.answer}" / 원점수=${a.rawScore} / 기록점수=${a.cappedScore}`);
        });
      }
      return lines.join("\n");
    }).join("\n\n");
  }

  function formatRequirementsBlock(requirementsResult) {
    if (!requirementsResult || !requirementsResult.length) return "(요구사항 없음)";
    return requirementsResult.map((r) => `- ${r.requirement}: ${r.verdict}${r.note ? ` (${r.note})` : ""}`).join("\n");
  }

  /** 문제×레벨 매트릭스. LLM 없이 session.results로부터 결정론적으로 계산한다. */
  function buildMatrix(session) {
    return session.results.map((r, i) => ({
      index: i, title: r.topic.title, outcome: r.outcome,
      cells: POCScoring.AXIS_IDS.map((axis) => {
        const lvl = r.levels.find((l) => l.axis === axis);
        return lvl
          ? { axis, attempted: true, score: lvl.finalScore, hints: lvl.hintsUsedFinal, passed: lvl.passed }
          : { axis, attempted: false };
      }),
    }));
  }

  /** 재시험 대상. LLM 판단이 아니라 scoring-config의 규칙(D-poc: L1 실패만)으로 결정한다 --
   *  이 값은 채점 신뢰성과 직결돼 LLM 자유 서술에 맡기지 않는다. */
  function buildRetestList(session) {
    return session.results
      .map((r, i) => ({ index: i, title: r.topic.title, failedAxis: r.failedAxis, outcome: r.outcome }))
      .filter((r) => POCScoring.needsRetest(r.failedAxis));
  }

  /**
   * @param {object} setup     1단계 산출물
   * @param {object} analysis  2단계 산출물
   * @param {object} session   3단계 산출물
   * @param {object} hooks     {onProgress}
   * @returns {Promise<object>} report 페이로드
   */
  async function runReportStage(setup, analysis, session, hooks) {
    const teachesBlock = TeachesSource.formatTeachesBlock(setup.teaches);
    const transcriptStage = POCStage.getStage("p04-6");
    const transcriptBlock = formatTranscriptBlock(session).slice(0, transcriptStage.truncation.transcript_block);
    const analysisBlock = JSON.stringify(analysisDocForPrompt(analysis.analysisDoc)).slice(0, transcriptStage.truncation.analysis_block);
    const requirementsBlock = formatRequirementsBlock(analysis.requirementsResult);

    hooks.onProgress("보고서 작성 중...");
    const llm = await POCStage.call("p04-6", {
      teaches_block: teachesBlock, transcript_block: transcriptBlock,
      analysis_block: analysisBlock, requirements_block: requirementsBlock,
    }, { model: session.model, onProgress: hooks.onProgress });

    const report = {
      llm,
      matrix: buildMatrix(session),
      retestTopics: buildRetestList(session),
      requirementsResult: analysis.requirementsResult,
      generated_at: new Date().toISOString(),
    };

    await saveReportRun(setup, session, report).catch((e) => hooks.onProgress(`DB 저장 실패(결과는 화면에 남아있음): ${e.message}`));
    return report;
  }

  // report.html은 독립된 페이지 로드라 3단계의 sessionDbRun(모듈 변수)을 이어받지 못한다 --
  // 새 run 행을 하나 열어 최종 보고서를 그 자체로 완결된 기록으로 남긴다(3단계 턴 기록과는
  // 별도 행, artifacts.kind="report"로 구분). 이 저장소의 다른 "best-effort, 실패해도
  // 화면은 안 막음" 관용을 그대로 따른다.
  async function saveReportRun(setup, session, report) {
    if (!LabDB.isConfigured()) return;
    const startedAt = new Date();
    const run = await LabDB.startRun({
      pipeline: "p04", model: session.model,
      input_meta: { teach_ids: setup.teaches.map((t) => t.id), topic_count: session.results.length },
      overrides: {},
    });
    await LabDB.saveRun({
      run_id: run.id, pipeline: "p04", model: session.model,
      input_meta: { teach_ids: setup.teaches.map((t) => t.id), topic_count: session.results.length },
      overrides: {}, rubric_overridden: false,
      artifacts: [{ kind: "report", content: report }],
      started_at: startedAt.toISOString(), finished_at: new Date().toISOString(), status: "done",
    });
  }

  return { runAnalysisStage, runSessionStage, runReportStage, resolveFiles, resolveFileContents };
})();
