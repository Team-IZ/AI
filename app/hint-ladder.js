// 문제 1개(teach + 근거 코드)의 L1~L4 세부질문(p04-4)을 답변을 보기 전에 동결하고,
// 오답이 확정될 때마다 그 답변을 근거로 힌트 1개를 생성한다(p04-7).
//
// D4 개정(2026-07-30): 질문은 여전히 미리 동결(안 바뀜), 힌트는 답변 기반으로 옮겨감.
// 근거/트레이드오프는 app/scoring-config.js의 hintLadder 선언부 주석 참고.
const HintLadder = (() => {
  function buildAxisIntentBlock() {
    return POCScoring.AXIS_IDS.map((id) => {
      const axis = POCScoring.AXES[id];
      return `- ${id} (${axis.label}): ${axis.question_intent}`;
    }).join("\n");
  }

  function formatTopicBlock(topic) {
    return [
      `제목: ${topic.title}`,
      topic.rationale ? `선정 이유: ${topic.rationale}` : null,
      `근거 위치: ${CodeFragment.formatRef(topic.code_ref)}`,
    ].filter(Boolean).join("\n");
  }

  function formatTeachBlock(teach) {
    if (!teach) return "(연결된 teach 없음)";
    const pages = TeachesSource.formatPages(teach.source_pages && teach.source_pages.length ? teach.source_pages : teach.unit_pages);
    return `Unit ${teach.unit_id} ${teach.unit_title} · ${teach.name} (${pages})\n${teach.summary || ""}`;
  }

  // p04-4용과 문구를 다르게 둔다 -- p04-7 프롬프트는 이 블록을 "힌트 문장에 직접 인용하지
  // 말고 의도로만 참고하라"는 맥락에서 받으므로, 여기서부터 "겨냥할 개념"이라는 프레이밍을
  // 명시해 둔다(질문 생성용 formatTeachBlock과 문구가 겹쳐도 되지만 목적을 분리해 둠).
  function formatTeachIntentBlock(teach) {
    if (!teach) return "(연결된 teach 없음 -- 코드와 질문만 근거로 힌트를 만들 것)";
    return `이 힌트가 학생을 다시 이끌어야 할 개념: ${teach.name}\n(참고용 요약 -- 문장을 그대로 인용하지 말 것: ${teach.summary || "요약 없음"})`;
  }

  /** 모델 출력의 levels를 검증 가능한 형태로 정규화. 형태가 어긋나면 null.
   *  D4 개정: 힌트는 더 이상 여기서 안 나온다 -- axis+question만 검증한다. */
  function normalizeLevels(rawLevels) {
    if (!Array.isArray(rawLevels) || rawLevels.length !== POCScoring.AXIS_IDS.length) return null;
    const byAxis = new Map(rawLevels.map((l) => [l && l.axis, l]));
    const out = [];
    for (const axisId of POCScoring.AXIS_IDS) {
      const lvl = byAxis.get(axisId);
      if (!lvl || typeof lvl.question !== "string" || !lvl.question.trim()) return null;
      out.push({ axis: axisId, question: lvl.question.trim() });
    }
    return out;
  }

  /**
   * @param {object} topic  p04-3이 고른 {teach_id, title, rationale, code_ref}
   * @param {object} ctx    {teach, files, model, onProgress}
   * @returns {Promise<object>} {topic, levels, code_ref, flagged, reason?}
   */
  async function freezeQuestionSet(topic, ctx) {
    const fragment = CodeFragment.extractFragment(ctx.files, topic.code_ref);
    const codeBlock = fragment.valid
      ? fragment.text
      : "(근거 코드 파편을 확인할 수 없음 -- code_ref가 실제 파일 범위를 벗어남)";
    const maxRegenerations = POCStage.paramDefault("p04-4", "max_regenerations") ?? 2;

    let levels = null;
    let lastReason = null;
    for (let attempt = 0; attempt <= maxRegenerations; attempt++) {
      const data = await POCStage.call("p04-4", {
        topic_block: formatTopicBlock(topic),
        code_block: codeBlock,
        axis_intent_block: buildAxisIntentBlock(),
        teach_block: formatTeachBlock(ctx.teach),
        code_ref: CodeFragment.formatRef(fragment.valid ? fragment : topic.code_ref),
      }, { model: ctx.model, onProgress: ctx.onProgress });

      const candidate = normalizeLevels(data.levels);
      if (!candidate) {
        lastReason = [{ axis: "-", field: "levels", matched: [`형태 불일치 (levels ${Array.isArray(data.levels) ? data.levels.length : "없음"}개)`] }];
      } else {
        const violations = QuestionGuard.checkQuestionSet(candidate);
        if (!violations.length) { levels = candidate; break; }
        lastReason = violations;
      }
      if (ctx.onProgress && attempt < maxRegenerations) {
        ctx.onProgress(`"${topic.title}" 질문 재생성 (${attempt + 1}/${maxRegenerations}) -- ${lastReason.map((v) => v.field).join(", ")}에서 선택지/형식 위반`);
      }
    }

    if (!levels) {
      return { topic, levels: null, flagged: true, reason: lastReason, frozen_at: new Date().toISOString() };
    }
    return {
      topic,
      levels,
      code_ref: fragment.valid ? { file: fragment.file, lines: fragment.lines, symbol: fragment.symbol } : topic.code_ref,
      code_block: codeBlock,
      flagged: false,
      frozen_at: new Date().toISOString(),
    };
  }

  function formatAttemptsBlock(question, attempts) {
    if (!attempts || !attempts.length) return `질문: ${question}\n(아직 답변 없음)`;
    return attempts.map((a, i) =>
      `시도 ${i + 1}${a.hint ? ` (힌트 ${i}: ${a.hint})` : ""}:\n` +
      `  질문: ${a.question || question}\n` +
      `  답변: ${a.answer}\n` +
      `  채점: ${a.cappedScore}점 -- missing: ${a.missing || "(없음)"} / evidence: ${a.evidence || "(없음)"}`
    ).join("\n\n");
  }

  /** 규칙 위반·빈 응답이 재시도 후에도 계속될 때 쓰는 결정론적 폴백 -- 힌트 미생성을 구조적으로 막는다. */
  function fallbackHint(hintLevel, codeRef) {
    const ref = CodeFragment.formatRef(codeRef);
    if (Number(hintLevel) === 1) {
      return `방금 답변에서 다루지 않은 부분이 있습니다. ${ref}을 다시 살펴보고, 이전 답변에 빠진 관점이 무엇인지 스스로 점검해보세요.`;
    }
    return `질문 범위를 좁혀 다시 묻습니다. ${ref}에서 가장 핵심적인 한 부분만 골라, 그 부분만 설명해보세요.`;
  }

  /**
   * 오답 확정 직후 호출 -- 그 레벨의 질문+지금까지의 시도 전문을 근거로 힌트 1개를 생성한다.
   * @param {object} p  {axis, hintLevel(1|2), question, attempts, teach, codeBlock, codeRef, model, onProgress}
   * @returns {Promise<{lv:number, kind:string, text:string, generated:boolean}>}
   *          generated:false면 폴백 문장이 쓰였다는 뜻(감사 목적으로 기록에 남긴다).
   */
  async function generateHint(p) {
    const spec = POCScoring.hintSpecFor(p.hintLevel);
    const codeRefStr = CodeFragment.formatRef(p.codeRef);
    const maxRegenerations = POCStage.paramDefault("p04-7", "max_regenerations") ?? 1;

    for (let attempt = 0; attempt <= maxRegenerations; attempt++) {
      let data;
      try {
        data = await POCStage.call("p04-7", {
          hint_level: `${p.hintLevel} (${spec.kind})`,
          hint_strength_spec: spec.spec,
          question: p.question,
          attempts_block: formatAttemptsBlock(p.question, p.attempts),
          teach_intent_block: formatTeachIntentBlock(p.teach),
          code_block: p.codeBlock,
          code_ref: codeRefStr,
        }, { model: p.model, onProgress: p.onProgress });
      } catch (e) {
        if (p.onProgress) p.onProgress(`힌트 생성 호출 실패(${attempt + 1}/${maxRegenerations + 1}): ${e.message}`);
        continue;
      }
      const text = typeof data.hint === "string" ? data.hint.trim() : "";
      if (text && !QuestionGuard.check(text).violated) {
        return { lv: p.hintLevel, kind: spec.kind, text, generated: true };
      }
      if (p.onProgress && attempt < maxRegenerations) {
        p.onProgress(`힌트 재생성 (${attempt + 1}/${maxRegenerations}) -- ${text ? "선택지/형식 위반" : "빈 응답"}`);
      }
    }

    if (p.onProgress) p.onProgress("힌트 생성이 규칙을 계속 위반해 폴백 문장으로 대체됩니다.");
    return { lv: p.hintLevel, kind: spec.kind, text: fallbackHint(p.hintLevel, p.codeRef), generated: false };
  }

  return { freezeQuestionSet, generateHint, fallbackHint, buildAxisIntentBlock, normalizeLevels, formatTeachIntentBlock };
})();
