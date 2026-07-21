import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from idiom_filter import resolve_lang, load_patterns, _find_file_content  # noqa: E402
from subrubric_hook import weight_for  # noqa: E402

# D53 (subrubric_hook.py에 전체 WHY/COST/EXIT): 서브축 값을 그냥 더하지 않고
#   subrubric_hook.weight_for(axis, sub_axis)로 가중치를 곱한 뒤, 그 axis의 실제
#   가능한 최대치로 정규화해 0~12 스케일을 유지한다(_weighted_sum/_normalize).
#   가중치가 전부 기본값(1.0)이면 이 정규화는 항등함수라 D27~D35 시절 출력과 100%
#   동일하다 — 재귀 피드백이 쌓이기 전까지는 아무 동작 변화가 없다는 뜻(하위호환 보장).

# D35: 4서브축 분해를 문헌 근거로 재검토 — POC_TEST.md(D31 문서)가 "이 4개가 정말 해당
#   construct를 대표하는 분해인지 외부 검증이 없다"고 지적한 데 대한 응답. 웹서치로 확인한
#   근거와 그에 따라 실제로 바꾼 지점:
#   - design_intent.location_signal(파일명 힌트) → 문헌 근거 전무, 가장 약한 서브축이었음.
#     Self-Admitted Technical Debt(SATD) 탐지 연구(Potdar & Shihab, 2014, MSR;
#     Maldonado & Shihab, 2015)는 "의도성의 근거 = 코드 코멘트의 명시적 시인/설명 언어"임을
#     방법론으로 직접 검증함 → rationale_signal()로 교체(아래).
#   - design_intent.repetition_consistency → Allamanis & Sutton, "Mining Idioms from
#     Source Code"(FSE 2014, FREQTALS 알고리즘)이 빈도 기반 관용구 탐지를 표준 방법론으로
#     확립 → 그대로 유지, 근거만 보강.
#   - design_intent.idiom_conformance_reverse → 같은 idiom-mining 문헌이 뒷받침 → 유지.
#   - question_value의 4축(트레이드오프/repo_specificity/idiom_contamination/ladder) →
#     고전 검사이론의 변별도 지수(discrimination index, point-biserial correlation)가
#     tradeoff_existence를, Haladyna & Downing item-writing guideline(1989, rev. 2002;
#     Rodriguez & Albano 2017 22-rule 축약판)의 "construct-irrelevant variance 최소화"
#     원칙이 repo_specificity를, CAT(Computerized Adaptive Testing)의 item exposure
#     control 문헌(반복 노출된 문항은 변별력을 잃고 보안 문제가 된다는 원칙)이
#     idiom_contamination_reverse를 각각 직접 뒷받침 → 이 축은 재설계 불필요.
#   - risk의 trigger_confidence를 severity 서브축과 그냥 더하던 것 → CVSS는 Exploitability
#     지표와 Impact 지표를 애초에 분리하고, FindBugs/SpotBugs 문헌은 "confidence(신뢰도)는
#     rank/severity(심각도)와 별개 축"이라고 명시 — 신뢰도와 심각도를 단순 합산하면 "신뢰도
#     낮은데 그럴듯해 보이는" finding이 "신뢰도 높은 경미한" finding과 같은 점수를 받는
#     문제가 생김 → score_risk()를 신뢰도가 심각도를 게이팅하는 구조로 변경(아래).
#   WHY: POC_TEST.md D31 이후 지적("Signal→Construct 매핑의 외부 검증 없음")에 그대로
#        응답 — 근거 없는 휴리스틱(location_signal)은 교체하고, 근거가 이미 있던 축은
#        유지하되 인용을 코드에 남겨 다음 리뷰가 "왜 이 4개인가"를 바로 확인 가능하게 함
#   COST: rationale_signal()이 repo_root를 요구해 파일 I/O가 추가됨(기존엔 diffusion
#        finding만 파일을 읽었는데 이제 cognition-isolation/tier-b-risk도 읽음).
#        risk 축은 상/중/하 3단계로는 여전히 confidence·severity 두 구성개념을 완전히
#        분리해서 보여주지 못함(subrubric.sub에는 남지만 최종 bucket은 하나)
#   EXIT: rationale_signal의 인디케이터 정규식은 영어/한국어 일부만 커버 — 오탐/누락이
#        쌓이면 idiom_hook류 재귀 학습 루프로 교체 검토. risk를 confidence/severity
#        두 필드로 완전히 분리하고 싶으면 apply_subrubric()의 반환 스키마만 바꾸면 됨
#        (score_risk 내부 로직은 이미 분리돼 있어 재구성 비용 낮음)
RATIONALE_INDICATOR_RE = re.compile(
    r"(intentionally|on purpose|by design|deliberately|의도적|일부러|의도했)",
    re.I,
)
DEBT_INDICATOR_RE = re.compile(
    r"(TODO|FIXME|HACK\b|workaround|quick[- ]fix|임시\s*방편|나중에\s*(고치|수정))",
    re.I,
)


def rationale_signal(file, repo_root):
    """SATD 탐지 방법론(Potdar & Shihab 2014)을 근거로, 파일 내용에서 명시적 설계근거
    언어(rationale) vs 부채 시인 언어(debt)를 스캔한다. 파일을 못 찾으면 "none".

    한계(문서화): 라인 단위가 아니라 파일 전체 스캔 — finding이 가리키는 지점 근처가
    아니라 파일 어디든 매치되면 신호로 잡힌다(SATD 원 연구는 코멘트 단위로 분석하지만
    이 파이프라인은 라인 번호를 추적하지 않아 파일 단위로 근사).
    """
    if not file or not repo_root:
        return "none"
    content = _find_file_content(repo_root, file)
    if content is None:
        return "none"
    if DEBT_INDICATOR_RE.search(content):
        return "debt"
    if RATIONALE_INDICATOR_RE.search(content):
        return "rationale"
    return "none"

# D29: SUBRUBRIC_DRAFT.md(D27/D28)를 score_findings.py에 실제로 연결하는 구현체
#   WHY: 초안 문서만으로는 실행 가능한 채점이 안 됨 — 인지 블록이 이미 만들어내는 필드
#        (fan_in/pattern_key/matched_text/트리거/idiom 로그)만으로 4서브축×3축을 계산해
#        새 스캔 로직 추가 없이 감사 가능한 점수를 만든다
#   COST: score_findings.py의 각 finding 생성 지점마다 증거값을 조립하는 코드가 늘어남
#        (finding 종류별로 쓸 수 있는 증거가 달라 공용 함수 하나로 뭉치지 못함)
#   EXIT: 증거 조립이 너무 번잡해지면 finding 종류별 evidence dataclass로 리팩터링 검토
#
# D30: 서브축 점수 컷오프는 SUBRUBRIC_DRAFT.md와 동일하게 상=9~12/중=5~8/하=0~4 유지
#   WHY: 문서와 코드가 다른 값을 쓰면 팀 합의가 깨짐 — 이 구현의 목적은 문서를 실행
#        가능하게 만드는 것이지 새 기준을 도입하는 게 아님
#   COST: 컷오프가 실측 데이터 없이 여전히 임의값(D28와 동일 COST를 계승)
#   EXIT: THRESHOLDS만 바꾸면 판단 블록 전체가 재보정됨(단일 지점)
THRESHOLDS = {"상": 9, "중": 5}


def bucket(total):
    """0~12 총점을 기존 상/중/하 문자열로 재매핑한다(D28).

    idiom_filter.DOWNGRADE_MAP은 정확히 "상"/"중"/"하" 키만 인식하므로, 여기서 반환하는
    문자열은 절대 다른 텍스트를 덧붙이면 안 된다(하위호환 필수 조건).
    """
    if total >= THRESHOLDS["상"]:
        return "상"
    if total >= THRESHOLDS["중"]:
        return "중"
    return "하"


def _clamp(x):
    return max(0, min(3, x))


def _weighted_sum(axis, sub):
    """sub(서브축→0~3 원점수)에 subrubric_hook의 현재 가중치를 곱해 (가중합, 가중가능최대치)를 반환."""
    total, max_total = 0.0, 0.0
    for key, value in sub.items():
        w = weight_for(axis, key)
        total += value * w
        max_total += 3 * w
    return total, max_total


def _normalize(total, max_total):
    """가중합을 원래 0~12 스케일로 되돌린다 — 가중치가 전부 1.0이면 항등함수(D53)."""
    if max_total <= 0:
        return 0
    return round(total / max_total * 12)


def idiom_evidence(pattern_key, file):
    """pattern_key가 있으면 해당 언어 idiom 저장소에서 (status, confirmations)를 조회한다.

    pattern_key가 없거나 언어 판별이 안 되면 ("none", 0) — "이 finding은 알려진 프레임워크
    관용 패턴과 아직 매치되지 않았다"는 뜻이지 "관용 패턴이 아니다"라는 확정 판정이 아니다.
    """
    if not pattern_key or not file:
        return "none", 0
    lang = resolve_lang(file)
    if lang is None:
        return "none", 0
    data = load_patterns(lang)
    for p in data["patterns"]:
        if p["id"] == pattern_key:
            return p["status"], p["confirmations"]
    return "none", 0


def score_design_intent(*, repetition, idiom_status, rationale, mitigation_present):
    """설계의도 축 — 이 구조가 의식적 설계 결정인가, 방치/누락인가.

    repetition: 이 패턴/구조가 repo 내 몇 곳에서 더 나타나는가(정수, 0~3+ clamp).
                근거: Allamanis & Sutton, "Mining Idioms from Source Code"(FSE 2014)
    idiom_status: idiom_evidence()의 status — confirmed일수록 "생각해서 짠 것"이 아니라
                  "그냥 따라간 컨벤션"에 가까우므로 역채점(confirmed=0, candidate=1, none=3).
                  근거: 위와 동일 idiom-mining 문헌
    rationale: rationale_signal()의 반환값("rationale"/"debt"/"none"). 근거:
               Potdar & Shihab(2014) Self-Admitted Technical Debt — 코멘트의 명시적
               설명 언어가 의도성의 직접 증거, 부채 시인 언어(TODO/HACK 등)는 반대 증거
    mitigation_present: 완화/방어 시도가 코드에 보이는가(bool). 해당 없는 finding
                         종류는 None → 중립값(1) 부여. (문헌 근거 약함 — 코드 내 방어
                         조치의 존재를 "의도적 엔지니어링 흔적"으로 보는 보조 신호일 뿐,
                         D35에서 교체 검토했으나 대체할 문헌을 찾지 못해 유지)
    """
    sub = {
        "repetition_consistency": _clamp(repetition),
        "idiom_conformance_reverse": {"confirmed": 0, "candidate": 1, "none": 3}[idiom_status],
        "rationale_signal": {"rationale": 3, "none": 1, "debt": 0}[rationale],
        "mitigation_present": 1 if mitigation_present is None else (3 if mitigation_present else 0),
    }
    total, max_total = _weighted_sum("design_intent", sub)
    return _normalize(total, max_total), sub


def score_question_value(*, tradeoff_signal, repo_specificity, idiom_downgrade_votes, ladder_richness):
    """질문가치 축 — 이 finding을 물었을 때 이해도 격차가 실제로 드러나는가.

    D35 문헌 근거(POC_TEST.md D31 이후 "construct 대표성 외부검증 없음" 지적에 대한 응답,
    이 축은 재설계 없이 근거만 보강):
    tradeoff_signal: "왜 이렇게 했나"에 정답이 하나로 안 정해지고 대안이 실재하는가(bool).
                     근거: 고전 검사이론의 변별도 지수(discrimination index, point-biserial
                     correlation) — 정답이 자명한 문항은 변별력이 낮다는 원리를 그대로 적용
    repo_specificity: 범용 지식으론 답 못하고 이 코드를 봐야만 답할 수 있는가(bool).
                      근거: Haladyna & Downing item-writing guideline(1989, 2002 개정) —
                      construct-irrelevant variance(측정하려는 능력과 무관한 사유로 맞히는
                      경우) 최소화 원칙
    idiom_downgrade_votes: idiom_evidence()의 confirmations — 과거에 "그냥 컨벤션"으로
                           강등된 표수가 많을수록 질문가치가 낮아지므로 역채점. 근거:
                           Computerized Adaptive Testing의 item exposure control 문헌 —
                           반복 노출/확인된 문항은 변별력을 잃고 시험 보안 문제가 된다는 원칙
    ladder_richness: Depth Ladder 7단계를 채울 정보량(정수, 0~3+ clamp). 근거: Haladyna
                     guideline의 "단일 인지 수준 기반 출제" 및 Bloom's Taxonomy의 고차
                     인지 수준 요구 원칙(팀 원 Notion 문서에서 이미 검토·기각된 방법론이지만
                     "깊이 있는 질문일수록 변별력이 높다"는 원칙 자체는 유효)
    """
    sub = {
        "tradeoff_existence": 3 if tradeoff_signal else 0,
        "repo_specificity": 3 if repo_specificity else 0,
        "idiom_contamination_reverse": _clamp(3 - idiom_downgrade_votes),
        "ladder_richness": _clamp(ladder_richness),
    }
    total, max_total = _weighted_sum("question_value", sub)
    return _normalize(total, max_total), sub


def score_risk(*, trigger_confirmed, exposure_client, scenario_specific, spread_count):
    """위험도 축 — 실제 보안/신뢰성 문제인가.

    D35 문헌 근거로 공식을 변경함(POC_TEST.md D31 검증 과정에서 발견): 기존엔
    trigger_confidence(신뢰도)를 exposure/scenario/spread(심각도) 서브축과 단순 합산했다.
    CVSS는 애초에 Exploitability 지표와 Impact 지표를 분리하고, FindBugs/SpotBugs
    문헌은 confidence(신뢰도)를 rank/severity(심각도)와 별개 축으로 다룬다 — 두 문헌
    모두 "신뢰도와 심각도를 그냥 더하지 않는다"는 원칙을 공유한다. 단순 합산은 "신뢰도
    낮은데 그럴듯해 보이는" finding이 "신뢰도 높은 경미한" finding과 같은 점수를 받는
    문제를 만든다. 그래서 신뢰도가 심각도 총점을 게이팅하는 구조로 바꿨다:
    신뢰도가 낮음(False)으로 확정되면 심각도가 아무리 높아도 총점을 "하" 구간으로 제한하고,
    신뢰도가 높음(True)이면 심각도에 가산점을, 불명(None)이면 심각도만 그대로 반영한다.

    trigger_confirmed: 이 히트가 오탐 억제 필터를 통과했고 매치 조건 자체도 신뢰도가
                        높은가(bool). Tier B 미해당 finding은 None → 중립
    exposure_client: 문제 코드/데이터가 외부 접근 가능 경로에 있는가(bool). 불명은 None → 중립값(1)
    scenario_specific: matched_text만으로 구체적 공격/오류 시나리오 서술이 가능한가(bool)
    spread_count: 동일 위험 패턴이 몇 개 파일에 반복되는가(정수, 0~3+ clamp)
    """
    severity_sub = {
        "exposure_scope": 1 if exposure_client is None else (3 if exposure_client else 0),
        "scenario_specificity": 3 if scenario_specific else 0,
        "spread_scope": _clamp(spread_count),
    }
    confidence_raw = 1 if trigger_confirmed is None else (3 if trigger_confirmed else 0)
    sub = {"trigger_confidence": confidence_raw, **severity_sub}

    # D53: 게이팅 판단(오탐 확정 시 "하" 상한)은 가중치와 무관하게 원본 신뢰도로 결정한다 —
    #   "이 서브축이 통계적으로 덜 믿을만하다"(가중치)와 "이 finding은 오탐이다"(신뢰도
    #   판정 자체)는 서로 다른 질문이라 가중치를 게이트 조건에 섞으면 안 된다. 덧셈에는
    #   가중치를 적용하되(_weighted_sum), 게이트는 confidence_raw로만 연다/닫는다.
    total, max_total = _weighted_sum("risk", sub)
    normalized = _normalize(total, max_total)
    if confidence_raw == 0:
        # CVSS/FindBugs 원칙: 신뢰도가 낮으면(오탐으로 판정) 심각도가 아무리 높아도
        # "하" 구간(THRESHOLDS["중"] 미만)으로 제한한다 — 심각도와 신뢰도를 곱하듯 게이팅
        normalized = min(normalized, THRESHOLDS["중"] - 1)
    return normalized, sub


def apply_subrubric(finding, design_intent_evidence, question_value_evidence, risk_evidence):
    """세 축을 계산해 finding에 상/중/하 문자열(하위호환) + subrubric 감사 트레일을 채운다."""
    di_total, di_sub = score_design_intent(**design_intent_evidence)
    qv_total, qv_sub = score_question_value(**question_value_evidence)
    rk_total, rk_sub = score_risk(**risk_evidence)

    finding["design_intent"] = bucket(di_total)
    finding["question_value"] = bucket(qv_total)
    finding["risk"] = bucket(rk_total)
    finding["subrubric"] = {
        # D53: weights를 함께 노출 — 지금 어떤 서브축이 discounted 상태인지 사람이
        # subrubric_hook.py 상태 파일을 따로 안 열어봐도 finding 하나만 보고 알 수 있게 함
        "design_intent": {"sub": di_sub, "weights": {k: weight_for("design_intent", k) for k in di_sub}, "total": di_total},
        "question_value": {"sub": qv_sub, "weights": {k: weight_for("question_value", k) for k in qv_sub}, "total": qv_total},
        "risk": {"sub": rk_sub, "weights": {k: weight_for("risk", k) for k in rk_sub}, "total": rk_total},
    }
    return finding
