// 질문·힌트 문장에 선택지가 섞여 들어가는 걸 막는다.
//
// D-poc7 (실측 결함, 2026-07-28): 사용자가 실제로 목격한 사고 -- 질문에 보기 3개가 들어가
// 있었고, 학생이 "저는 3번"이라고 답해 대안비교 축에서 5점을 받았다. 대안을 제시한 게
// 아니라 고른 것이다. graduated prompting에서 "선택지 제시"는 힌트 사다리의 세 번째
// 단계(가장 강한 도움)에 해당하는데, 그게 질문 본문에 무심코 섞여 나오면 모든 학생이
// 매 문제 힌트3을 공짜로 받는 셈이 되어 자력/보조 구분 자체가 무너진다.
//   WHY 정규식 기반: LLM에게 "선택지 넣지 마라"고 프롬프트로만 지시하는 건 이미 p04-4에
//   해 놨지만(prompt_manifest.json), 지시를 어기는 사례가 실측으로 확인됐으므로 생성물을
//   신뢰하지 않고 사후 검사한다.
//   COST: 정규식은 오탐(정상 문장을 걸러냄)과 미탐(교묘한 선택지를 통과시킴) 둘 다 가능한
//   근사치다. 오탐 시 재생성 비용이 들고, 미탐 시 이 사고가 다시 일어날 수 있다.
//   EXIT: 재생성이 max_regenerations(기본 2)회 안에 못 벗어나면 flagged로 표시해 사람이
//   보게 남긴다 -- 조용히 통과시키지 않는다.
const QuestionGuard = (() => {
  const PATTERNS = [
    // 원문자/괄호 번호 열거: ①②③ 또는 (1)(2)(3)
    /[①②③④⑤]/,
    /\([1-5]\)\s*\S+.*\([1-5]\)/s,
    // 줄머리 번호/알파벳 매김이 2개 이상 -- 단답 예시 하나("1) 이렇게 하면...")는 통과시키고
    // 목록으로 나열된 경우만 잡기 위해 최소 2회 등장을 요구한다.
    /(^|\n)\s*[1-5][).]\s+\S+[\s\S]*(^|\n)\s*[1-5][).]\s+\S+/m,
    /(^|\n)\s*[A-DA-D][).]\s+\S+[\s\S]*(^|\n)\s*[A-DA-D][).]\s+\S+/m,
    // "A와 B 중", "다음 중", "보기 중에서"류 지시어
    /[A-Za-z가-힣0-9]+\s*(와|과)\s*[A-Za-z가-힣0-9]+\s*중/,
    /다음\s*중/,
    /보기\s*(중|에서)/,
  ];

  /** @returns {{violated:boolean, matched:string[]}} */
  function check(text) {
    const s = String(text || "");
    const matched = [];
    for (const re of PATTERNS) {
      const m = s.match(re);
      if (m) matched.push(m[0].slice(0, 40));
    }
    return { violated: matched.length > 0, matched };
  }

  /** 질문 세트(levels[].question + hints[].text) 전체를 검사. */
  function checkQuestionSet(levels) {
    const violations = [];
    for (const lvl of levels || []) {
      const q = check(lvl.question);
      if (q.violated) violations.push({ axis: lvl.axis, field: "question", matched: q.matched });
      for (const h of lvl.hints || []) {
        const r = check(h.text);
        if (r.violated) violations.push({ axis: lvl.axis, field: `hint(lv${h.lv})`, matched: r.matched });
      }
    }
    return violations;
  }

  return { check, checkQuestionSet };
})();
