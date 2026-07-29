// 문제 1개(teach + 근거 코드)에 대해 L1~L4 세부질문과 고정 힌트 2단을 한 번에 생성해
// 동결한다 (p04-4). D4의 구현 지점 -- 이 함수가 반환한 이후에는 어떤 답변을 받아도
// 질문·힌트 텍스트가 다시 바뀌지 않는다.
const HintLadder = (() => {
  const REQUIRED_HINT_LVS = [1, 2];

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

  /** 모델 출력의 levels를 검증 가능한 형태로 정규화. 형태가 어긋나면 null. */
  function normalizeLevels(rawLevels) {
    if (!Array.isArray(rawLevels) || rawLevels.length !== POCScoring.AXIS_IDS.length) return null;
    const byAxis = new Map(rawLevels.map((l) => [l && l.axis, l]));
    const out = [];
    for (const axisId of POCScoring.AXIS_IDS) {
      const lvl = byAxis.get(axisId);
      if (!lvl || typeof lvl.question !== "string" || !lvl.question.trim()) return null;
      const hints = Array.isArray(lvl.hints) ? lvl.hints : [];
      if (hints.length !== REQUIRED_HINT_LVS.length) return null;
      const sortedHints = REQUIRED_HINT_LVS.map((lv) => hints.find((h) => Number(h.lv) === lv));
      if (sortedHints.some((h) => !h || typeof h.text !== "string" || !h.text.trim())) return null;
      out.push({
        axis: axisId,
        question: lvl.question.trim(),
        hints: sortedHints.map((h) => ({ lv: h.lv, kind: h.kind || "", text: h.text.trim() })),
      });
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
      code_ref: fragment.valid ? { file: fragment.file, lines: fragment.lines } : topic.code_ref,
      code_block: codeBlock,
      flagged: false,
      frozen_at: new Date().toISOString(),
    };
  }

  return { freezeQuestionSet, buildAxisIntentBlock, normalizeLevels };
})();
