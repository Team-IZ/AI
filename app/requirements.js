// 1단계에서 입력한 요구사항 목록을 제출된 코드에 대해 P/F로 판정한다 (p04-2).
const Requirements = (() => {
  function formatBlock(requirements) {
    if (!requirements || !requirements.length) return "(요구사항 없음)";
    return requirements.map((r, i) => `${i + 1}. ${r}`).join("\n");
  }

  /**
   * @param {string[]} requirements
   * @param {object} files
   * @param {object} opts  {model, onProgress, maxAttempts, maxChars}
   * @returns {Promise<Array<{requirement,verdict,evidence,note}>>}
   */
  async function judge(requirements, files, opts = {}) {
    if (!requirements || !requirements.length) return [];
    const manifest = LabApp.getManifest();
    const stage = manifest.pipelines.p04.stages.find((s) => s.id === "p04-2");
    const maxChars = (stage.truncation && stage.truncation.code_block) || 12000;
    const code_block = CodeFragment.buildCodeBlock(files, { maxChars });
    const data = await POCStage.call("p04-2", {
      requirements_block: formatBlock(requirements),
      code_block,
    }, opts);
    const results = Array.isArray(data.results) ? data.results : [];
    // 개수가 어긋나면(모델이 일부를 빠뜨림) 위치 기준으로 이어붙이지 않고, 누락분을
    // 명시적으로 "판정 실패"로 남긴다 -- 조용히 순서가 밀려 엉뚱한 요구사항에 판정이
    // 붙는 것보다 낫다.
    return requirements.map((req, i) => {
      const r = results[i];
      if (!r) return { requirement: req, verdict: "F", evidence: null, note: "모델이 이 요구사항에 대한 판정을 반환하지 않음" };
      return { requirement: req, verdict: r.verdict === "P" ? "P" : "F", evidence: r.evidence || null, note: r.note || "" };
    });
  }

  return { judge, formatBlock };
})();
