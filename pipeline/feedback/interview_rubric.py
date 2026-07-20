# D54: Project Ownership Verification 인터뷰(examples/lms/d_plan/, POC 발표문)에서 쓴
#   3축(구조_인지도/트레이드오프_인지도/대안_탐색_능력) 점수가 지금까지는 레벨별 정의 없이
#   1~5 정수만 매겨져 있었다 — subrubric.py(D27)가 정적분석 3축(design_intent/
#   question_value/risk)에 대해 이미 겪은 문제("근거 없는 한 줄 판정")를 인터뷰 채점 쪽도
#   똑같이 겪고 있었다는 뜻.
#   WHY: 사용자가 축별 5단계 레벨 설명을 명시적으로 제공 — 이걸 파이썬 dict로 코드화해두면
#        ①미래 인터뷰 채점이 이 기준을 그대로 재사용 가능 ②이미 매긴 점수(9개: 3개 의사결정
#        × 3축)를 레벨 텍스트와 실제 대조해 "왜 이 점수인가"를 사후적으로 검증 가능해짐.
#        subrubric.py처럼 서브축으로 더 잘게 쪼개지는 않음 — 이건 정적분석 신호가 아니라
#        사람(LLM)이 대화를 읽고 총체적으로 판단하는 축이라 잘게 쪼갤 근거 신호가 없다.
#        대신 "왜 이 레벨인가"를 텍스트 근거(evidence)와 함께 명시하도록 강제한다.
#   COST: 레벨 설명 자체가 사용자가 준 텍스트를 그대로 옮긴 것이라, "3점과 4점의 경계가
#        어디인가" 같은 애매한 케이스(예: 반례에 빠르게 반응했지만 완전한 이해는 아닌 경우)의
#        판정은 여전히 채점자 재량. 서브루브릭처럼 하위 신호로 더 분해하지 않아 판정 재현성은
#        subrubric.py보다 낮음
#   EXIT: 경계 케이스가 반복되면 각 레벨을 다시 2~3개 하위 신호로 쪼개 subrubric.py 방식
#        (근거 필드 → 가중합)으로 승격 가능. RUBRIC 딕셔너리 구조만 유지하면 됨
#
# D57: 3축을 요구사항정의서 FR-04-01의 5축(코드이해/설계논리/대안비교/반례대응/자기수정)
#   체계에 맞춰 정리
#   WHY: frontend/mockups/dashboard.html(교육생 상세 페이지)이 이미 FR-04-01 5축 레이더
#        차트를 전제로 만들어져 있는데, 정작 채점 코드는 3축뿐이었다. 게다가 기존 3축 중
#        "트레이드오프_인지도"는 이름과 달리 레벨 설명 5개가 전부 "반례" 기준으로 쓰여 있어
#        (아래 RUBRIC 원문 참고 — "반례를 스스로 먼저 언급", "반례를 이해하고" 등) 실질적으로
#        이미 FR의 "반례 대응"과 동일한 축이었다. 즉 전면 재설계가 아니라 ①이름-내용 불일치
#        정정 ②원래 없던 2축(설계논리/자기수정) 신설이 필요했던 것:
#          - 구조_인지도      → 코드_이해   (내용: 구성요소·데이터흐름 설명 능력, 그대로 대응)
#          - 트레이드오프_인지도 → 반례_대응   (내용이 원래부터 반례 대응이었음, 이름만 재명명)
#          - 대안_탐색_능력    → 대안_비교   (내용 동일, FR 용어에 맞춰 재명명)
#          - 설계_논리 (신규) — "왜 이렇게 설계했는가"는 기존 3축 어디에도 없었음
#          - 자기_수정 (신규) — reflection_signal.evaluate_reflection()이 4개 서브신호로
#            이미 boolean 판정을 하고 있었지만(D34), 그건 "이 답변을 다음 단계로 넘길지"
#            결정하는 실시간 분기용이지 최종 리포트용 1~5점 척도가 아니었다. 서브신호 매칭
#            개수를 그대로 1~5 등급 초안으로 승격해 재사용(auto_score_self_correction()) —
#            레벨 4 경계는 현재 reflection_present=True 판정 기준(자기오류인식+optional 2개)과
#            정확히 일치하도록 맞춤. D37의 Codex 실측 케이스(Bookshelf.jsx, "정성적으로 매우
#            우수한 reflection"인데 confirmed 패턴 불일치로 0/4)에 이 함수를 그대로 돌려보면
#            1/5점이 나옴 — 새 버그가 아니라 D37/D34가 이미 기록한 재현율 문제가 1~5 등급
#            에도 그대로 드러난 것(아래 COST)
#   COST: 기존 3축의 dict 키(구조_인지도 등)는 하위호환을 위해 이름을 바꾸지 않고 그대로 둠 —
#        examples/lms/d_plan/interview_rubric_verification.md에 이미 "구조_인지도=3" 같은
#        기록이 있어 키를 바꾸면 그 문서의 근거가 깨짐(과거 기록을 소급 수정하지 않는다는
#        원칙, D22와 같은 결). 대신 FR_AXIS_ALIAS로 대응관계만 명시 — RUBRIC엔 구 이름 3개와
#        신규 이름 2개가 섞여 있어 겉보기 일관성은 낮아짐. auto_score_self_correction()도
#        "초안"일 뿐 최종 확정은 여전히 사람/LLM 몫이라는 원칙(D54 WHY)은 유지 — 이 세션엔
#        실제 학생 답변으로 자동 초안과 사람 판정이 얼마나 어긋나는지 비교 검증은 못 함
#   EXIT: 다음 인터뷰 회차부터 새 이름으로 직접 채점하고 싶으면 RUBRIC 키를 FR_AXIS_ALIAS
#        값으로 바꾸고, 옛 검증 문서엔 "구 명칭=신 명칭" 각주만 남기면 됨. auto_score_self_
#        correction()의 임계값이 실측과 안 맞으면 이 함수만 고치면 됨(RUBRIC 구조는 불변)

from reflection_signal import evaluate_reflection  # noqa: E402

RUBRIC = {
    "구조_인지도": {
        5: "구성 요소 간 관계와 데이터 흐름을 정확히 설명",
        4: "흐름은 맞지만 일부 연결 관계 불명확",
        3: "각 구성 요소는 알지만 연결 관계 설명 불가",
        2: "개별 요소 설명은 가능하나 전체 구조 파악 안 됨",
        1: '"그냥 이렇게 했습니다" 수준',
    },
    "트레이드오프_인지도": {
        5: "반례를 스스로 먼저 언급하거나, 던졌을 때 즉각 인지",
        4: "반례를 이해하고 부분적 대응 가능",
        3: "반례를 들었을 때 이해는 하나 대안 없음",
        2: '반례를 인지했지만 "문제없다"고 주장',
        1: "반례를 던져도 이해 불가",
    },
    "대안_탐색_능력": {
        5: "구체적인 기술적 대안을 즉시 제시",
        4: "대안 방향은 명확하나 구현 방법 일부 불명확",
        3: "대안이 존재한다는 것은 알지만 구체화하지 못함",
        2: "대안 탐색 시도 자체를 하지 않고 현재 방식을 그대로 고수",
        1: "대안이라는 개념 자체를 이해하지 못함",
    },
    "설계_논리": {
        5: "설계 의도와 제약조건(성능/시간/팀 상황 등)을 연결해 명확히 설명",
        4: "설계 의도는 설명하나 제약조건과의 연결은 부분적",
        3: '"이렇게 하면 될 것 같아서"처럼 의도는 있으나 근거가 약함',
        2: "의도를 물으면 사후적으로 짜맞추는 티가 남(질문 전엔 의식하지 못했던 흔적)",
        1: "왜 이렇게 했는지 설명 자체를 못함",
    },
    "자기_수정": {
        5: "자기오류인식+이유설명+새판단+구체적개선안 4개 신호 전부 스스로 제시",
        4: "자기오류인식을 포함해 3개 이상 신호 확인(reflection_signal.py 현재 True 판정 기준)",
        3: "자기오류인식은 있으나 이유설명·새판단·개선안 중 1개만 확인",
        2: "자기오류인식 없이 새판단이나 개선안만 언급(무엇이 틀렸는지는 인정하지 않음)",
        1: "반례를 제시해도 오류를 전혀 인정하지 않음",
    },
}

AXES = tuple(RUBRIC.keys())

# FR-04-01(요구사항정의서) 5축 이름과의 대응관계 — D57
FR_AXIS_ALIAS = {
    "구조_인지도": "코드_이해",
    "트레이드오프_인지도": "반례_대응",
    "대안_탐색_능력": "대안_비교",
    "설계_논리": "설계_논리",
    "자기_수정": "자기_수정",
}
_ALIAS_TO_AXIS = {v: k for k, v in FR_AXIS_ALIAS.items()}


def _resolve_axis(axis):
    """RUBRIC의 원래 키든 FR-04-01 별칭이든 둘 다 받아들인다(D57)."""
    if axis in RUBRIC:
        return axis
    if axis in _ALIAS_TO_AXIS:
        return _ALIAS_TO_AXIS[axis]
    raise ValueError(
        f"unknown axis '{axis}', must be one of {AXES} "
        f"or their FR-04-01 별칭 {tuple(FR_AXIS_ALIAS.values())}"
    )


def describe(axis, score):
    """axis(5개 중 하나, 원래 이름/FR 별칭 둘 다 허용)의 score(1~5) 레벨 설명 텍스트를 반환."""
    axis = _resolve_axis(axis)
    if score not in RUBRIC[axis]:
        raise ValueError(f"score must be 1~5, got {score}")
    return RUBRIC[axis][score]


def score_card(axis, score, evidence):
    """채점 결과 1건을 감사 가능한 형태로 만든다 — subrubric.py의 finding["subrubric"]와
    동일한 목적(점수만 있고 근거가 없는 상태를 막는 것)."""
    axis = _resolve_axis(axis)
    return {
        "axis": axis,
        "fr_axis": FR_AXIS_ALIAS[axis],
        "score": score,
        "criterion": describe(axis, score),
        "evidence": evidence,
    }


def auto_score_self_correction(text):
    """'자기_수정' 축의 1~5점 **초안**을 reflection_signal.evaluate_reflection()의 서브신호
    매칭 개수로부터 만든다. 다른 4축과 마찬가지로 최종 점수는 evidence와 함께 사람/LLM이
    확정해야 한다(D54 WHY) — 이 함수는 그 출발점만 제공한다.

    레벨 4 경계는 evaluate_reflection()의 현재 reflection_present=True 판정 기준
    (self_error_recognition 필수 + optional 2개 이상, D34)과 정확히 일치하도록 맞췄다.
    """
    result = evaluate_reflection(text)
    if not result["required_ok"]:
        return 2 if result["optional_matches"] > 0 else 1
    if result["optional_matches"] >= 3:
        return 5
    if result["optional_matches"] >= 2:
        return 4
    return 3
