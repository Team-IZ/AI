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
    // D6 (2026-07-30): L3/L4 순서 확정 -- L3=대안비교, L4=반례대응 (사용자 지시로 이전
    // 순서(L3=반례한계, L4=대안)에서 자리를 맞바꿈).
    //   WHY: 사용자가 세부질문 진행 순서를 명시적으로 재확정 -- "설계 논리를 물은 다음엔
    //   대안과 비교해 정당화시키고, 그래도 안 되면 마지막으로 반례를 들이대 방어시킨다"는
    //   순서(대안 비교가 반례 방어보다 먼저)로 판단한 것으로 해석. 값 단계 서술 내용
    //   자체는 안 바꾸고(각 축이 재는 것은 동일), order와 축 id/label만 맞바꿨다.
    //   COST: 기존에 "L3=반례, L4=대안"을 전제로 쌓인 세션 기록(있다면)의 axis id와
    //   지금부터의 axis id가 의미상 자리가 바뀐다 -- 과거 기록을 이 순서로 재해석하려면
    //   axis id 매핑을 수동으로 뒤집어야 한다(id 문자열 자체가 L3_반례한계->L4_반례대응,
    //   L4_대안->L3_대안비교로 바뀌었으므로 단순 문자열 치환 불가, 의미 매핑 필요).
    //   EXIT: 다시 되돌리려면 이 두 블록의 order(3<->4)와 키 이름만 원래대로 스왑.
    L3_대안비교: {
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
    L4_반례대응: {
      order: 4,
      label: "반례 대응",
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

  // ── 힌트 사다리 강도 정의 (D4 개정, 2026-07-30) ────────────────────────────────
  //
  // 최초 설계(2026-07-28)는 힌트를 문제 시작 시 질문과 함께 미리 고정했다 -- 근거는
  // "같은 실력의 두 학생이 다른 힌트를 받으면 그 차이가 학생 차이가 아니라 생성 차이가
  // 된다"였다. 사용자가 실사용 후 이걸 뒤집었다: 힌트는 학생이 그 레벨에서 실제로 낸
  // 답변(질문+답변 전문+채점의 missing/evidence)을 본 뒤에 생성해야 한다 -- 안 그러면
  // 힌트가 그 학생이 실제로 놓친 지점을 겨냥하지 못한다.
  //   WHY 바뀐 설계가 여전히 유효한가: 학생 간 "비교 가능성"의 근거가 이동했을 뿐 없어진
  //   게 아니다. 이전엔 힌트 텍스트가 글자 단위로 동일한 것이 근거였다면, 지금은
  //   ①사다리 단계 수(레벨당 최대 2회, thresholds.maxHintsPerLevel)
  //   ②각 단계의 강도 정의(아래 hintLadder -- 정답/선택지/새 사실 금지가 모든 학생에게
  //     동일하게 강제됨)
  //   ③점수 상한(hintCaps)
  //   이 세 가지가 모든 학생에게 동일하다는 것이 근거다. "몇 번째 힌트에서, 얼마나 강한
  //   도움을 받고서야 통과했는가"라는 측정값 자체는 그대로 유지된다 -- 오히려 표준
  //   graduated prompting 구현(힌트가 학생의 실제 오류에 반응하는 것)에 더 가까워졌다.
  //   COST: 힌트 텍스트가 학생마다 달라지는 게 이제 **의도된 설계**다 -- "왜 힌트가
  //   다르냐"는 더 이상 버그 리포트가 아니다. 또한 오답마다 LLM 호출이 1회 늘어난다
  //   (레벨당 최대 2회 추가) -- session.html의 타이핑 인디케이터가 그 대기를 가시화한다.
  //   EXIT: 다시 고정 방식으로 되돌리려면 hint-ladder.js의 generateHint() 호출을
  //   freezeQuestionSet() 안으로 옮기고 spec을 프롬프트에 미리 굽기만 하면 된다 --
  //   이 설정 구조 자체는 두 방식 모두에서 재사용 가능하게 설계했다.
  //
  // spec 문자열은 app/prompt_manifest.json의 p04-7 {hint_strength_spec}으로 그대로
  // 주입된다 -- 힌트 문구·강도를 바꾸려면 이 파일과 그 매니페스트만 고치면 된다(사용자
  // 요구: "프롬프트 파일 내용만 수정하면 바뀌도록").
  const hintLadder = {
    1: {
      kind: "관점 되짚기",
      label: "힌트 1",
      spec: "학생 답변에서 말하지 않은 관점을 짚어 같은 질문을 다시 물어라. 새로운 사실이나 정답을 제시하지 마라 -- 학생이 이미 답변에 쓴 내용 중 무엇이 빠졌는지만 드러내라.",
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
  const axisWeights = { L1_코드기술: 1.0, L2_설계논리: 1.0, L3_대안비교: 1.0, L4_반례대응: 1.0 };
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
    AXES, AXIS_IDS, thresholds, hintCaps, hintLadder, autonomy, onLevelFail, retest,
    axisWeights, axisWeightsProvenance,
    capForHints, applyCap, autonomyFor, passed, buildRubricBlock, nextAxis, needsRetest, hintSpecFor,
  };
})();
