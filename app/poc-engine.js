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
    hooks.onProgress("코드 분석 문서 작성 중...");
    const docStage = POCStage.getStage("p04-1");
    const docCodeBlock = CodeFragment.buildCodeBlock(files, { maxChars: docStage.truncation.code_block });
    const analysisDoc = await POCStage.call("p04-1", {
      teaches_block: teachesBlock, findings_block: findingsBlock, code_block: docCodeBlock,
    }, { model, onProgress: hooks.onProgress });

    // decision_points는 모델이 스스로 지목한 {file,lines}다 -- 실제 파일과 대조해 검증하고,
    // 지어낸 위치는 화면에 노출하지 않는다(코드 파편이 곧 근거이므로 여기서 거르지 않으면
    // "존재하지 않는 코드"를 근거라고 보여주게 된다).
    const decisionPoints = (Array.isArray(analysisDoc.decision_points) ? analysisDoc.decision_points : [])
      .map((dp) => ({ ...dp, fragment: CodeFragment.extractFragment(files, { file: dp.file, lines: dp.lines }) }));
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
      analysis_block: JSON.stringify(analysisDoc).slice(0, 8000),
      findings_block: findingsBlock,
      question_count: questionCount,
    }, { model, onProgress: hooks.onProgress });

    let topics = Array.isArray(topicsRaw.topics) ? topicsRaw.topics : [];
    // 검증: teach_id가 실제로 선택된 teaches 안에 있어야 하고, 서로 달라야 한다. 어긴 topic은
    // 버린다 -- 존재하지 않는 teach를 참조하는 문제를 만들 수는 없다.
    const teachIds = new Set(setup.teaches.map((t) => t.id));
    const seenTeach = new Set();
    topics = topics.filter((t) => {
      if (!teachIds.has(t.teach_id) || seenTeach.has(t.teach_id)) return false;
      seenTeach.add(t.teach_id);
      return true;
    }).slice(0, questionCount);
    if (topics.length < questionCount) {
      hooks.onProgress(`⚠ 문제 ${questionCount}개를 요청했으나 유효한 문제 ${topics.length}개만 확보됨 (teach 참조 검증 실패분 제외)`);
    }

    // ── p04-4: 문제별 L1~L4 + 고정 힌트 ─────────────────────────────────────
    const teachesById = new Map(setup.teaches.map((t) => [t.id, t]));
    const questionSets = [];
    for (const topic of topics) {
      hooks.onProgress(`"${topic.title}" 질문·힌트 생성 중...`);
      const qs = await HintLadder.freezeQuestionSet(topic, {
        teach: teachesById.get(topic.teach_id), files, model, onProgress: hooks.onProgress,
      });
      if (qs.flagged) hooks.onProgress(`⚠ "${topic.title}" 질문이 선택지 금지 규칙을 계속 위반해 flagged 상태로 남음 -- 검토 필요`);
      questionSets.push(qs);
    }

    const finishedAt = new Date();
    const analysis = {
      analysisDoc, decisionPoints, requirementsResult, topics, questionSets,
      findings, fileCount, repoRef: repoRef || null,
      submissionMethod: setup.submission.method,
      model,
      started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
    };

    hooks.onProgress(`결과가 팀 DB에 저장됨(best-effort)`);
    await saveAnalysisRun(setup, analysis).catch((e) => hooks.onProgress(`DB 저장 실패(결과는 화면에 남아있음): ${e.message}`));

    hooks.onStatus("완료", "done");
    return analysis;
  }

  // best-effort DB 기록. app/p04_schema.sql이 아직 적용되지 않았으면 CHECK 제약(23514)에
  // 걸려 실패하는데, 그건 이 함수가 아니라 그 마이그레이션의 책임이다 -- 여기서는 실패해도
  // 화면 흐름을 막지 않는다(이 저장소의 saveFailedRun/maybeSaveRun과 동일한 관용).
  async function saveAnalysisRun(setup, analysis) {
    if (!LabDB.isConfigured()) return;
    await LabDB.startRun({
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
   * @param {object} analysis  2단계 산출물(POCState.loadAnalysis())
   * @param {object} hooks     {
   *   onProgress(msg), onStatus(text,kind),
   *   onTopicStart({index,topic}), onTopicEnd({index,outcome,failedAxis}),
   *   onLevelResult({topicIndex,axis,hintsUsed,score,passed}),
   *   getAnswer({topicIndex,topic,axis,question,hintsUsed,hintText,codeRef,codeBlock}) -> Promise<string>
   * }
   * @returns {Promise<object>} session 페이로드
   */
  async function runSessionStage(analysis, hooks) {
    const model = analysis.model;
    const results = [];

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

      for (const lvl of qs.levels) { // HintLadder.normalizeLevels가 이미 L1~L4 순서로 정렬해둠
        const attempts = [];
        let hintsUsed = 0;
        let cappedScore = 0;
        let passed = false;

        for (;;) {
          const hintText = hintsUsed === 0 ? null : lvl.hints[hintsUsed - 1].text;
          const answer = await hooks.getAnswer({
            topicIndex: ti, topic: qs.topic, axis: lvl.axis, question: lvl.question,
            hintsUsed, hintText, codeRef: qs.code_ref, codeBlock: qs.code_block,
          });
          hooks.onProgress(`${qs.topic.title} · ${POCScoring.AXES[lvl.axis].label} 채점 중...`);
          const graded = await gradeLevel({
            axis: lvl.axis, question: lvl.question, hintsUsed, hintText, answer,
            codeBlock: qs.code_block, codeRefStr, model, onProgress: hooks.onProgress,
          });
          const cap = POCScoring.applyCap(graded.score, hintsUsed);
          cappedScore = cap.capped;
          attempts.push({
            hintsUsed, hint: hintText, question: lvl.question, answer,
            rawScore: cap.raw, cappedScore: cap.capped, capApplied: cap.capApplied,
            evidence: graded.evidence, missing: graded.missing,
          });
          await logSessionTurn(analysis, { topicIndex: ti, axis: lvl.axis, hintsUsed, question: lvl.question, answer, score: cap.capped });

          passed = POCScoring.passed(cappedScore);
          hooks.onLevelResult({ topicIndex: ti, axis: lvl.axis, hintsUsed, score: cappedScore, passed });
          if (passed) break;
          if (hintsUsed >= POCScoring.thresholds.maxHintsPerLevel) break; // 힌트 소진, 미달 -- 아래에서 처리
          hintsUsed++;
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

    const session = { results, model, started_at: new Date().toISOString(), finished_at: new Date().toISOString() };
    await saveSessionRun(analysis, session).catch((e) => hooks.onProgress(`DB 저장 실패(결과는 화면에 남아있음): ${e.message}`));
    return session;
  }

  let sessionDbRun = null;
  async function saveSessionRun(analysis, session) {
    if (!LabDB.isConfigured() || !sessionDbRun) return; // 세션 시작조차 실패했으면 마무리 저장도 생략
    await LabDB.saveRun({
      run_id: sessionDbRun.id, pipeline: "p04", model: session.model,
      input_meta: { topic_count: analysis.questionSets.length },
      overrides: {}, rubric_overridden: false,
      artifacts: [{ kind: "session_results", content: session.results }],
      started_at: session.started_at, finished_at: session.finished_at, status: "done",
    });
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
    const analysisBlock = JSON.stringify(analysis.analysisDoc).slice(0, transcriptStage.truncation.analysis_block);
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

  return { runAnalysisStage, runSessionStage, runReportStage, resolveFiles };
})();
