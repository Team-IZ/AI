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
      let verdict = r.verdict === "P" ? "P" : "F";
      let note = r.note || "";
      // D-fix (redteam audit H4, 2026-08-04): decision_points/topics는 이미
      // CodeFragment.extractFragment로 실제 파일과 대조하는데(D-poc10), 이 판정만 모델의
      // evidence를 무검증으로 채택했다 -- 제출 코드에 가짜 "## 규칙" 섹션을 심어 P를
      // 유도하는 프롬프트 인젝션의 최종 착지점이 여기였다. P 판정은 evidence.symbol이
      // 실제 소스에 존재할 때만 살아남는다; 못 찾으면(지어낸 코드거나 file이 틀렸으면)
      // F로 강등한다 -- "있을 법한 P"보다 "근거 확인된 F"가 안전하다.
      if (verdict === "P") {
        const grounded = CodeFragment.extractFragment(files, {
          file: r.evidence && r.evidence.file,
          symbol: r.evidence && r.evidence.symbol,
        });
        if (!grounded.valid) {
          verdict = "F";
          note = `근거 코드를 확인할 수 없어 F로 강등(${grounded.reason})${note ? " -- " + note : ""}`;
        }
      }
      return { requirement: req, verdict, evidence: r.evidence || null, note };
    });
  }

  return { judge, formatBlock };
})();

// D-fix (redteam audit H4, 2026-08-04): same export-guard pattern as the sibling
// code-fragment.js in this directory, added so node --test can require() the real
// judge() to verify the grounding check without duplicating its logic in a test.
if (typeof module !== "undefined" && module.exports) module.exports = Requirements;
