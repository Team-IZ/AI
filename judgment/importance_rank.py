import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from subrubric import THRESHOLDS  # noqa: E402

# D194 (findings_ranking_plan.md Phase 0): the 3 subrubric axes (design_intent/question_value/
# risk) were computed independently per finding, but nothing anywhere combined them into a
# single importance order -- confirmed by grep across judgment/*.py and docs/lab/*.js before
# writing this. The one place downstream code DID need an order
# (hookfile/generate_hook_file.py's priority_rank dict) already broke silently: idiom_filter.py
# mutates the priority string on downgrade, so idiom-demoted findings miss the dict lookup and
# fall to its `.get(priority, 9)` default -- confirmed live in examples/study_match's
# judgment_output.json (1 occurrence of the mutated string).
#   WHY: buckets (상/중/하) collapse almost all variance the pipeline actually has -- measured
#        over the full 40-repo/104-finding example corpus, 87/104 (83.7%) share a single bucket
#        triple (중,상,하), while the underlying raw 0-12 totals already in
#        finding["subrubric"][axis]["total"] resolve to 16 distinct triples. D28's own EXIT
#        already sanctioned consuming raw totals instead of buckets for exactly this reason.
#   COST: a finding's overall importance now depends on 3 equal, unmeasured-provisional weights
#        (see RANK_WEIGHT_* below) until the pending labeler sprint
#        (benchmarks/judgment_precision_labels.jsonl, 50 findings, dual-labeler fields still
#        null) produces real Precision@1 data to fit them -- see benchmarks/fit_rank_weights.py
#        (not yet written; Phase 2 of findings_ranking_plan.md, out of scope for this change).
#   EXIT: `judgment/rank_weights/rank_weights.json` is the single point to recalibrate once
#        labels exist -- no code change needed, just replace the file (same D5 pattern as
#        idiom_patterns.json: data separate from logic, git revert is the rollback story).
#
# D-tierb2 (2026-07-31, D-tierb1의 랭킹 쪽 귀결): Tier B finding이 사라진 뒤 이 파일의
#   **코드는 한 줄도 바꾸지 않는다.** 대신 무엇이 조용히 무의미해졌는지를 여기 명시한다 --
#   이 결정 자체가 D-tierb1 리뷰에서 가장 놓치기 쉬운 부분이었기 때문이다.
#
#   측정(고정 fixture, JS 6파일 = entry/hub/peer 3 + 중복정의 1쌍, Tier B 3트리거 전부 발동):
#     제거 전: finding 5건, risk.total ∈ {9, 2}, trigger_confidence ∈ {3, 1}
#     제거 후: finding 2건, risk.total ∈ {2},    trigger_confidence ∈ {1}
#     남은 2건의 rank_score(6.667 / 5.667)와 상대 순서는 제거 전과 완전히 동일.
#
#   WHY 코드를 안 바꾸는가: subrubric.score_risk()에 True/False나 spread_count>0을 넘기던
#   유일한 호출부가 Tier B 블록이었다(score_findings.py의 D-tierb1 자리). 남은 finding
#   종류(cognition-isolation / architecture-diffusion / repeated-pattern /
#   duplicate-definition)는 넷 다 risk_evidence로 (None, None, False, 0) 상수를 넘긴다.
#   따라서 _sort_key()의 2번 슬롯(-risk_total)과 3번 슬롯(-trigger_confidence)은
#   **현재 개체군에서 항상 동률**이라 정렬에 기여하지 못하고, 실질 동점 처리기는
#   4번 슬롯(파일의 fan_in)으로, 최종 결정자는 id로 내려앉는다.
#   그렇다고 두 슬롯을 지우면, risk를 실제로 채우는 finding 소스가 다시 생기는 순간
#   (D-ground1의 LLM 분석 문서가 risks[]를 내놓으면 바로 그 경우다) 동점 처리가 조용히
#   사라진 채로 돌아간다 -- "지금 입력이 퇴화했다"는 것과 "이 로직이 틀렸다"는 다른 문제고,
#   전자를 이유로 후자처럼 코드를 지우면 되돌릴 때 근거가 남지 않는다.
#   rank_score 쪽도 같은 이유로 risk 항을 빼지 않는다: risk가 상수여도 가중합에 상수를
#   더하는 것이라 **순서는 불변**이고, 빼면 이미 Supabase runs에 저장된 과거 run의
#   rank_score와 숫자 비교가 불가능해진다(순서만 같고 값이 달라짐).
#   COST: (1) 웹 랩 p02-5 패널의 RANK_WEIGHT_RISK 슬라이더는 지금 **순서를 절대 바꾸지
#   못한다** -- 값(rank_score)만 흔들린다. 트레이니가 "가중치를 만졌는데 순위가 안 변한다"고
#   느낄 수 있고 그게 정상이라는 표시가 UI에는 없다. (2) tie_break_depth 분포가 이전보다
#   깊은 쪽(3~4)으로 쏠린다 -- 과거 run과 이 필드를 그대로 비교하면 안 된다.
#   (3) 3축 가중합의 1/3이 상수라 rank_score의 실질 해상도가 그만큼 줄었다.
#   EXIT: 라벨 스프린트(benchmarks/judgment_precision_labels.jsonl) 결과가 나오면
#   rank_weights.json에서 risk 가중치를 실측값으로 정한다 -- 그 전에 "risk가 상수니까
#   0으로 두자"고 손으로 정하는 것은 이 저장소가 금지하는 직관 기반 수치 결정이다
#   (D194 COST가 이미 같은 이유로 세 가중치를 1.0으로 묶어둔 상태).
#   회귀 방지: tests/test_rank_after_tier_b_drop.py가 위 측정치를 그대로 고정한다.

# D194: weights start equal and provisional -- same "named module constant, honestly marked
# unmeasured" style as D181's MAX_CONNECT_FILES. Overridable at runtime by the web lab's
# parameter editor (setattr(module, key, value) -- see the D-web-lab precedent at
# score_findings.py:92-100) and persistently by rank_weights.json.
RANK_WEIGHT_QV = 1.0
RANK_WEIGHT_RISK = 1.0
RANK_WEIGHT_DI = 1.0

RANK_WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "rank_weights", "rank_weights.json")


def _load_weights():
    """rank_weights.json이 있으면 그 값을, 없거나 읽기 실패하면 모듈 상수(동일가중치)를 쓴다."""
    try:
        with open(RANK_WEIGHTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        w = data.get("weights", {})
        return (
            w.get("question_value", RANK_WEIGHT_QV),
            w.get("risk", RANK_WEIGHT_RISK),
            w.get("design_intent", RANK_WEIGHT_DI),
        )
    except (OSError, ValueError):
        return RANK_WEIGHT_QV, RANK_WEIGHT_RISK, RANK_WEIGHT_DI


def _effective_question_value(finding):
    """idiom 강등된 finding은 subrubric 원점수를 감산하지 않고 상한을 캡한다 -- score_risk()의
    신뢰도 게이팅(subrubric.py, D35 CVSS/FindBugs 원칙)과 동일한 독트린: "관용 패턴으로 확정됨"은
    "질문가치가 조금 낮다"가 아니라 "질문가치 축 자체가 하 구간으로 게이팅된다"는 뜻이다.
    THRESHOLDS를 그대로 재사용하므로 D30 재보정 시 이 게이트도 같이 재보정된다."""
    sub = finding.get("subrubric", {})
    qv = sub.get("question_value", {})
    total = qv.get("total", 0)
    if qv.get("overridden_by") == "idiom_filter":
        return min(total, THRESHOLDS["중"] - 1)
    return total


def _sort_key(finding, fan_in):
    """정렬키(오름차순 정렬 시 "더 중요한 게 앞에 오도록" 전부 음수/역순으로 구성).

    1순위는 rank_score(3축 가중합) 그 자체 -- 이 함수는 호출 전에 finding["rank_score"]가
    이미 채워져 있다고 가정한다(apply_rank가 정렬 직전에 채움). 동점 처리 체인은 rank_score가
    "완전히 같을 때만" 개입한다(설계 문서 그대로): risk_total desc -> trigger_confidence desc ->
    finding.file의 fan_in desc -> id asc. Phase 1(S1~S5 근거 보강)은 이번 범위 밖 — 여기까지도
    안 갈리면 마지막은 항상 id로 완전히 결정된다(재현성 보장).

    D-tierb2: risk_total/trigger_confidence 두 슬롯은 Tier B 제거 이후 현재 finding
    개체군에서 항상 동일한 값이라 실질적으로 정렬에 기여하지 않는다(의도적으로 남겨둔
    상태 — 이유는 이 파일 상단 D-tierb2 참조). 실질 동점 처리기는 fan_in이다.
    """
    sub = finding.get("subrubric", {})
    risk_total = sub.get("risk", {}).get("total", 0)
    trigger_confidence = sub.get("risk", {}).get("sub", {}).get("trigger_confidence", 0)
    file_fan_in = fan_in.get(finding.get("file"), 0) if finding.get("file") else 0
    return (-finding["rank_score"], -risk_total, -trigger_confidence, -file_fan_in, finding.get("id", ""))


def apply_rank(findings, fan_in=None):
    """finding["subrubric"]의 3축 원점수(버킷 아님)를 가중합해 rank/rank_score/rank_evidence를
    붙이고, 그 순서로 findings를 재정렬해 반환한다.

    fan_in은 cognition/two_tier_scan.py의 Tier A 결과(score()가 이미 로컬 변수로 갖고 있음) --
    동점 처리 3단계(파일의 fan_in)에만 쓰인다. 안 넘기면(예: 독립 재채점 스크립트) 그 단계만
    건너뛰고 나머지 체인은 그대로 동작한다.
    """
    fan_in = fan_in or {}
    w_qv, w_risk, w_di = _load_weights()
    weight_sum = w_qv + w_risk + w_di

    scored = []
    for finding in findings:
        sub = finding.get("subrubric", {})
        qv_eff = _effective_question_value(finding)
        risk_total = sub.get("risk", {}).get("total", 0)
        di_total = sub.get("design_intent", {}).get("total", 0)
        gated = qv_eff != sub.get("question_value", {}).get("total", 0)

        rank_score = (
            (w_qv * qv_eff + w_risk * risk_total + w_di * di_total) / weight_sum
            if weight_sum > 0 else 0.0
        )

        finding["rank_score"] = round(rank_score, 3)
        finding["rank_evidence"] = {
            "weights": {"question_value": w_qv, "risk": w_risk, "design_intent": w_di},
            "terms": {"question_value": qv_eff, "risk": risk_total, "design_intent": di_total},
            "idiom_gate_applied": gated,
        }
        scored.append(finding)

    scored.sort(key=lambda f: _sort_key(f, fan_in))
    for i, finding in enumerate(scored, start=1):
        finding["rank"] = i
        finding["rank_evidence"]["tie_break_depth"] = None  # filled below if a tie was broken

    # tie_break_depth: how far down the sort key (rank_score -> risk_total ->
    # trigger_confidence -> fan_in -> id) this finding needed to go to separate from its
    # immediate predecessor -- 0 means rank_score alone decided it, purely informational
    # (rank_evidence is meant to make one finding self-explaining without cross-referencing
    # others, per D53's precedent of exposing weights directly on the finding).
    for i, finding in enumerate(scored):
        if i == 0:
            finding["rank_evidence"]["tie_break_depth"] = 0
            continue
        key_a, key_b = _sort_key(scored[i - 1], fan_in), _sort_key(finding, fan_in)
        depth = next((d for d in range(len(key_a)) if key_a[d] != key_b[d]), len(key_a))
        finding["rank_evidence"]["tie_break_depth"] = depth

    return scored
