// P04 채점 설정 -- 축(L1~L4) // 값(0~5) 단계별 정의 + 임계값 + 힌트 상한.
//
// D3: 채점 임계값·루브릭·가중치를 파이프라인 코드에서 분리해 이 파일 하나로 모은다.
//   WHY: "채점 임계값 하이퍼파라미터나 채점 로직은 모듈화로 빼놓고 설정 가능하게" (사용자
//   요구, 2026-07-28). 축×값 표 형태는 기존 P03의 p03-7 rubric과 같은 모양이라 렌더링
//   코드를 재사용할 수 있고, 프롬프트에 그대로 직렬화해 넣을 수 있다.
//   COST: 프롬프트 본문(app/prompt_manifest.json)과 루브릭(이 파일)이 두 곳에 나뉜다 --
//   축 이름을 바꾸면 양쪽 정합성을 직접 봐야 한다(자동 검사 없음).
//   EXIT: poc-engine.js가 매 채점 호출마다 buildRubricBlock()으로 이 파일에서 프롬프트
//   블록을 생성한다. 통합하려면 그 호출부를 매니페스트 문자열로 인라인하면 된다.
//
// ★ 수치의 출처 (CLAUDE.md §13 Data-First Numerics)
//   pass=3, maxHintsPerLevel=2, hintCaps={0:5,1:4,2:3} 는 실측값이 아니라 **사용자가
//   명시적으로 지정한 설계 파라미터**다(2026-07-28 확정: "힌트는 각 세부문제 당 2개까지만
//   제공하고 틀리면 아예 실패", "상한을 하더라도 5/4/3이 맞다"). 실측으로 보정할 대상이
//   아니라 제도(rule)이므로 그대로 고정한다.
//   반면 axisWeights 는 **미측정**이다 -- 실제 세션 데이터가 쌓이기 전에는 어떤 축이 더
//   변별력 있는지 알 수 없으므로 균등(1.0)으로 두고, 아래 재보정 조건을 명시한다.
const POCScoring = (() => {
  // ── 축 // 값 단계별로 ────────────────────────────────────────────────────────
  // 순서 = 실제 진행 순서. L1 실패 시 그 문제는 여기서 끝나고 다음 문제의 L1로 간다.
  const AXES = {
    L1_코드기술: {
      order: 1,
      label: "코드 기술",
      question_intent: "이 코드가 무엇을 어떻게 하는지 기술하게 한다",
      values: {
        5: "해당 코드가 하는 일과 데이터 흐름을 파일·함수·라인 단위로 정확히 기술한다",
        4: "전체 흐름은 정확하나 일부 구성 요소의 역할이 불명확하다",
        3: "개별 구성 요소는 설명하지만 그것들이 어떻게 이어지는지는 설명하지 못한다",
        2: "코드의 표면적 동작만 반복 진술한다(이름·주석을 읽는 수준)",
        1: "자기 코드인데 무엇을 하는지 특정하지 못한다",
        0: "무응답이거나 질문 대상 코드와 무관한 진술이다",
      },
    },
    L2_설계논리: {
      order: 2,
      label: "코드 설계논리",
      question_intent: "왜 이렇게 설계했는지, 그 판단의 근거를 말하게 한다",
      values: {
        5: "이 선택을 하게 만든 제약과 목표를 밝히고, 코드 구조가 그 제약에서 어떻게 도출되는지 연결해 설명한다",
        4: "이유를 대지만 제약과 실제 구조 사이의 연결이 한 단계 비어 있다",
        3: "관례·교육받은 방식 같은 외부 근거만 대고 자기 맥락에서의 이유는 대지 못한다",
        2: "이유를 묻는 질문에 동작 설명(L1 수준)으로 되돌아간다",
        1: "이유가 없다고 답하거나 그 자리에서 지어낸 근거를 댄다",
        0: "무응답이다",
      },
    },
    // D6 (2026-07-30, 사용자 지시) → D6-fix (2026-07-30, 팀 기획서 `poc-axis-order-fix.md`
    // 대조): L3/L4 순서 확정 -- L3=대안, L4=반례한계. 처음엔 순서(order)만 사용자 지시로
    // 맞바꿨는데, 축 id를 L3_대안비교/L4_반례대응으로 새로 짓는 바람에 백엔드(07_ENG의
    // axis_score.axis_code: ALTERNATIVE_COMPARISON/COUNTEREXAMPLE_RESPONSE로 이식 예정)
    // 팀이 명시한 정확한 식별자(L3_대안/L4_반례한계, label "반례 대응·한계")와 문자열이
    // 어긋났다 -- order/값(values)은 이미 맞았지만 키 이름은 팀 문서와 대조해서 다시 고침.
    //   WHY: 이 축 id 문자열은 AXIS_IDS로 앱 전체(질문 검증 normalizeLevels, 프롬프트
    //   축 열거, DB에 저장되는 stage_id)에 리터럴로 쓰인다 -- "이식" 시 팀이 이 정확한
    //   문자열을 참조하므로 순서만 맞고 이름이 다르면 여전히 안 맞는다.
    //   COST: 과거에 L3_반례한계/L4_대안(최초) 또는 L3_대안비교/L4_반례대응(D6 1차)를
    //   전제로 쌓인 세션 기록이 있다면 그 axis id들과 지금부터의 axis id가 다시 어긋난다.
    //   EXIT: 되돌리려면 이 두 블록의 order(3<->4)와 키 이름만 원래대로 스왑.
    L3_대안: {
      order: 3,
      label: "대안 비교",
      question_intent: "다른 선택지와 비교해 자기 선택을 정당화하게 한다",
      values: {
        5: "실제 대안을 특정하고 무엇을 얻고 무엇을 잃는지 자기 맥락 기준으로 비교해 선택을 정당화한다",
        4: "대안을 제시하지만 트레이드오프가 한쪽 방향으로만 서술된다",
        3: "대안의 이름만 대고 비교는 하지 못한다",
        2: "대안을 묻는데 현재 구현의 장점만 반복한다",
        1: "대안이 없다고 답한다",
        0: "무응답이다",
      },
    },
    L4_반례한계: {
      order: 4,
      label: "반례 대응·한계",
      question_intent: "이 설계가 깨지는 조건을 다루게 한다",
      values: {
        5: "자기 설계가 깨지는 조건을 스스로 특정하고, 그 조건에서 무엇이 먼저 무너지는지까지 말한다",
        4: "한계를 인정하고 조건도 대지만 구체성(규모·입력 형태·동시성 등)이 부족하다",
        3: "제시된 반례에는 대응하지만 스스로 한계를 찾아내지는 못한다",
        2: "반례를 방어하려다 코드 사실과 어긋나는 주장으로 넘어간다",
        1: "한계가 없다고 단언한다",
        0: "무응답이다",
      },
    },
  };

  const AXIS_IDS = Object.keys(AXES).sort((a, b) => AXES[a].order - AXES[b].order);

  // ── 진행·채점 임계값 (사용자 지정 제도값) ────────────────────────────────────
  const thresholds = {
    pass: 3,                 // 이 점수 이상이어야 다음 레벨로 진행
    maxHintsPerLevel: 2,     // 레벨(소문제)당 힌트 제공 횟수 상한. 소진 후 미달 = 실패
    questionsPerSubmission: 3,
  };

  // 힌트 사용 횟수 -> 그 레벨에서 받을 수 있는 점수 상한.
  // graduated prompting(Campione & Brown, 1987): "몇 번째 힌트에서 통과했는가"가 자력의
  // 측정값이 되도록, 도움을 받을수록 도달 가능한 최대치를 낮춘다.
  const hintCaps = { 0: 5, 1: 4, 2: 3 };

  // ── 힌트 생성 시점 (D4 개정 2026-07-30 → D7 절충 2026-07-30) ──────────────────
  //
  // 최초 설계(2026-07-28)는 힌트를 문제 시작 시 질문과 함께 미리 고정했다("동결").
  // 사용자가 실사용 후 답변 기반 즉시 생성으로 뒤집었다(D4 개정) -- 힌트가 학생이 그
  // 레벨에서 실제로 낸 답변을 본 뒤에 생성돼야 그 학생이 놓친 지점을 겨냥한다는 이유.
  // 그런데 같은 날 팀원 기획서(`poc-axis-order-fix.md`)가 "지금 계약은 동결 기준,
  // 세션 런타임은 채점만(턴당 LLM 호출 1개), 적응형 힌트는 나중에 별도 반영"이라고
  // 명시하며 정면으로 충돌했다.
  //   WHY 둘 다 남기는가: 사용자 지시(D4 개정)와 팀 계약(동결) 중 어느 게 맞는지는
  //   내가 결정할 사안이 아니다 -- 사용자가 "버튼으로 언제든 전환, 둘 다 테스트 가능하게
  //   유지"를 선택했다. 두 방식 다 실제로 동작하는 코드로 남기고 설정 하나로 스위칭한다.
  //   HOW 하나의 프롬프트로 둘 다 커버하는가: hint-ladder.js의 generateHint()를 두
  //   컨텍스트에서 같은 함수로 재사용한다 -- adaptive는 실제 attempts로, frozen은
  //   attempts:[]로 호출(질문 직후, 답변 없이 두 힌트를 미리 생성). 아래 spec 문구는
  //   "답변이 있다면 그걸 참고하고, 없다면(동결) 질문 자체에서 판단하라"로 두 경우 모두
  //   말이 되게 일반화했다 -- app/prompt_manifest.json의 p04-7도 동일하게 일반화.
  //   COST: frozen 모드는 팀 계약대로 턴당 채점 호출 1개만 쓰지만, adaptive는 오답마다
  //   힌트 생성 호출이 추가로 붙는다(레벨당 최대 2회) -- session.html의 타이핑
  //   인디케이터가 그 차이를 가시화한다. 두 모드를 유지하는 대가로 이 파일과
  //   poc-engine.js의 세션 루프에 분기가 하나씩 생긴다.
  //   EXIT: 팀과 최종 합의되면 default를 그 값으로 고정하고, index.html의 토글 UI와
  //   미사용 분기(hint-ladder.js freezeQuestionSet의 frozen 사전생성 루프 또는
  //   poc-engine.js의 adaptive 분기)를 지운다.
  const hintMode = {
    default: "frozen", // 팀 기획서의 "지금 계약"과 일치 -- 백엔드 이식 대상 기본값
    options: ["frozen", "adaptive"],
    labels: {
      frozen: "동결 (분석 단계에서 미리 생성 · 팀 현재 계약 · 턴당 LLM 호출 1개)",
      adaptive: "적응형 (오답 확정 직후 답변 기반 생성 · 턴당 LLM 호출 최대 3개)",
    },
  };

  // spec 문자열은 app/prompt_manifest.json의 p04-7 {hint_strength_spec}으로 그대로
  // 주입된다 -- 힌트 문구·강도를 바꾸려면 이 파일과 그 매니페스트만 고치면 된다(사용자
  // 요구: "프롬프트 파일 내용만 수정하면 바뀌도록"). frozen/adaptive 두 모드가 이
  // 문구를 공유하므로 "답변이 있다면 참고, 없다면(동결) 질문 자체로 판단"처럼 양쪽
  // 다 성립하게 써야 한다.
  const hintLadder = {
    1: {
      kind: "관점 되짚기",
      label: "힌트 1",
      spec: "이 질문에서 놓치기 쉬운 관점을 짚어 같은 질문을 다시 물어라. 학생 답변이 있다면 그 답변에서 다루지 않은 부분을 짚고, 아직 답변이 없다면(동결 사전생성) 이 질문 자체에서 흔히 놓치는 관점을 짚어라. 새로운 사실이나 정답을 제시하지 마라.",
    },
    2: {
      kind: "범위 좁힘",
      label: "힌트 2",
      spec: "질문의 범위를 한 단계 좁혀서 더 작은 하위 질문으로 다시 물어라. 원래 질문이 다루는 여러 요소 중 하나만 골라 집중시켜라 -- 여전히 정답이나 선택지를 주지 마라.",
    },
  };

  // 힌트 사용 횟수 -> 자력 판정 라벨. 점수와 별개로 기록해 ZPD("혼자 할 수 있는 것"과
  // "도움받으면 할 수 있는 것"의 거리)를 보고서에서 읽을 수 있게 한다.
  const autonomy = {
    0: { key: "self", label: "자력" },
    1: { key: "self_sustained", label: "자력 유지" },
    2: { key: "partial", label: "부분 자력" },
  };

  // 레벨 실패 시 동작. "endQuestion" = 남은 레벨을 X로 남기고 다음 문제의 L1로.
  const onLevelFail = "endQuestion";

  // 재시험 판정: L1(코드 기술)에서 힌트를 소진하고도 실패로 끝난 문제만 재시험 대상이다.
  //
  // 사용자 원 예시로 역산한 규칙 (2026-07-28):
  //   1번문제 (4,0/4,1/3,0/2,2) -- L1~L3 통과, L4는 힌트 소진 후 미달이지만 L4가 마지막
  //     레벨이라 "다음 레벨을 건너뛴다"는 효과가 아예 없다 -- 자연 종료. 재시험 대상 아님.
  //   2번문제 (3,0/2,2/X/X) -- L2에서 힌트 소진 후 미달, L3/L4는 X(건너뜀). 그래도
  //     재시험 대상 아님 -- L1(코드를 설명하는 최소 단계)은 통과했다.
  //   3번문제 (2,2/X/X/X) -- L1 자체에서 힌트 소진 후 미달. -> 재시험.
  //   결론: "완주 못 함"이 재시험 기준이 아니라 "L1(가장 기초 단계)에서도 막힘"이 기준이다.
  //   L2~L4 실패는 "상위 단계 미달"로 보고서에 남기되 재시험까지 요구하지 않는다.
  //
  // ★ 이 규칙은 사용자가 준 단일 예시에서 역산한 것이라 확정 사실이 아니라 가설이다 --
  //   실제 세션이 쌓이면(예: L2에서 반복적으로 막히는 학생들의 재응시 성과) 재검증할 것.
  const retest = {
    triggerAxis: "L1_코드기술",
    note: "이 축에서 힌트 소진 후 미달로 끝난 문제만 재시험 대상. L2~L4 실패는 보고서에만 표시.",
  };

  // ★ 미측정 -- 실제 세션이 쌓이기 전에는 축별 변별력을 알 수 없어 균등으로 둔다.
  //   재보정 조건: 완주 세션 30건 이상이 DB에 쌓이면 축별 점수 분포·통과율을 뽑아
  //   변별력 없는 축(전원 동일 점수)을 찾아 가중치를 조정한다. 그 전까지 이 값을
  //   임의로 바꾸지 말 것.
  const axisWeights = { L1_코드기술: 1.0, L2_설계논리: 1.0, L3_대안: 1.0, L4_반례한계: 1.0 };
  const axisWeightsProvenance = "unmeasured-provisional (2026-07-28) -- 균등. 완주 세션 30건 후 재보정";

  // ── 파생 함수 ────────────────────────────────────────────────────────────────

  /** 힌트 사용 횟수에 따른 점수 상한. 정의되지 않은 횟수는 가장 강한 힌트의 상한을 따른다. */
  function capForHints(hintsUsed) {
    const n = Math.max(0, Number(hintsUsed) || 0);
    if (hintCaps[n] !== undefined) return hintCaps[n];
    const known = Object.keys(hintCaps).map(Number).sort((a, b) => a - b);
    return hintCaps[known[known.length - 1]];
  }

  /** LLM 원점수에 힌트 상한을 적용한 기록 점수. 원점수는 그대로 따로 보존한다. */
  function applyCap(rawScore, hintsUsed) {
    const raw = Math.max(0, Math.min(5, Number(rawScore)));
    const cap = capForHints(hintsUsed);
    return { raw, capped: Math.min(raw, cap), cap, capApplied: raw > cap };
  }

  function autonomyFor(hintsUsed) {
    const n = Math.max(0, Number(hintsUsed) || 0);
    return autonomy[n] || { key: "assisted", label: "자력 아님" };
  }

  function passed(cappedScore) {
    return Number(cappedScore) >= thresholds.pass;
  }

  /** 채점 프롬프트에 넣을 축 1개의 값 단계 텍스트. */
  function buildRubricBlock(axisId) {
    const axis = AXES[axisId];
    if (!axis) throw new Error(`알 수 없는 축: ${axisId}`);
    let out = `### ${axis.label} (${axisId})\n목적: ${axis.question_intent}\n`;
    for (const score of [5, 4, 3, 2, 1, 0]) out += `  ${score}점: ${axis.values[score]}\n`;
    return out;
  }

  function nextAxis(axisId) {
    const i = AXIS_IDS.indexOf(axisId);
    return i >= 0 && i < AXIS_IDS.length - 1 ? AXIS_IDS[i + 1] : null;
  }

  /** @param {string|null} failedAxis  문제를 끝낸 축(끝까지 다 통과했으면 null) */
  function needsRetest(failedAxis) {
    return failedAxis === retest.triggerAxis;
  }

  /** 다음에 줄 힌트 단계(1 또는 2)의 강도 정의. 정의 없는 단계를 물으면 예외. */
  function hintSpecFor(hintLevel) {
    const spec = hintLadder[hintLevel];
    if (!spec) throw new Error(`정의되지 않은 힌트 단계: ${hintLevel}`);
    return spec;
  }

  return {
    AXES, AXIS_IDS, thresholds, hintCaps, hintLadder, hintMode, autonomy, onLevelFail, retest,
    axisWeights, axisWeightsProvenance,
    capForHints, applyCap, autonomyFor, passed, buildRubricBlock, nextAxis, needsRetest, hintSpecFor,
  };
})();
