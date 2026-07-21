import json
import os
import sys
from datetime import datetime, timezone

# D53: 서브루브릭 자체를 재귀 검증 가능하게 만듦 — idiom_hook/tier_b_hook/reflection_hook/
#   isolation_hook과 동일한 candidate→threshold→조정 구조를, "패턴"이 아니라 "서브축 가중치"에
#   적용한다. D50~D52(isolation_hook.py/reflection_hook.py)가 같은 세션 중 발견한
#   dedup+양방향 수정을 처음부터 반영했다(별도 D51급 후속 수정 없이 한 번에 구현).
#   WHY: subrubric.py(D27~D30, D35)는 4서브축을 전부 동일 가중치(=1.0)로 그냥 더한다 —
#        POC_TEST.md(D31)가 지적한 "이 4개 분해가 정말 construct를 대표하는지 외부 검증
#        없음"의 핵심 원인이 바로 이 고정 가중치다. 어떤 서브축은 특정 finding 유형에서
#        구조적으로 노이즈에 가까울 수 있는데(예: architecture-diffusion에 mitigation_present
#        중립값 1이 그냥 상수로 얹힘), 사람이 반복적으로 "이 서브축이 최종판정과 안 맞았다"고
#        피드백하면 그 서브축의 가중치를 실제로 낮출 수 있어야 서브루브릭이 정적 문서에서
#        살아있는 시스템이 된다. idiom_hook의 재귀 루프를 재사용하되, D51/D52가 실측으로
#        발견한 "같은 출처 반복 제출로 threshold 우회" 위험과 "한 번 discounted면 영원히
#        discounted"라는 단방향 문제를 처음부터 dedup+양방향 구조로 피했다.
#   COST: 가중치가 바뀌면 subrubric.py의 THRESHOLDS(9/12, 5/12)가 절대값 기준이라 더 이상
#        안 맞음 — _normalize()로 보정했지만(subrubric.py 참고), 가중치가 극단적으로
#        쏠리면(예: 서브축 1개만 살아남음) 정규화된 점수의 해상도가 낮아짐(0/3/6/9/12 중
#        하나로만 찍힘). D52와 동일한 COST도 계승: 재계산할 때마다 상태가 바뀔 수 있어
#        "이 서브축은 discounted다"라는 과거 보고가 다음 재계산에서 뒤집힐 수 있음(의도된
#        동작이지만 보고 시점을 항상 명시해야 함)
#   EXIT: 가중치 자체가 이상하게 수렴하면(예: 전부 discounted) THRESHOLDS를 손보거나,
#        promotion_threshold를 서브축별로 다르게 주는 것도 가능(weights.json에 이미
#        필드로 노출돼 있어 코드 수정 없이 값만 바꾸면 됨)
#
# 재귀 루프 구조 (축별로 독립 실행):
#   subrubric.py 실행 → (사람 또는 후속 리뷰가) 특정 finding에서 서브축 값이 최종 판정과
#   안 맞았다("misaligned") 또는 잘 맞았다("aligned")고 판정
#   → record_feedback(axis, sub_axis, verdict, ..., source_finding)으로 subrubric_weights/
#     <axis>/feedback_log.jsonl에 적재
#   → recursive_update(axis) 실행 → source_finding(또는 timestamp fallback)으로 dedup한 뒤,
#     같은 sub_axis에 독립 misaligned 출처가 threshold 이상이면 weight을 1.0→0.3으로
#     낮추고("discounted"), 미만이면 1.0으로 되돌린다("trusted") — 매번 양방향 재평가.
#     aligned 표는 게이팅에 안 쓰고 감사 기록으로만 남긴다
#   → 다음 subrubric.py 실행부터 score_design_intent/score_question_value/score_risk가
#     자동으로 최신 가중치를 반영

WEIGHTS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subrubric_weights")
AXES = ("design_intent", "question_value", "risk")
TRUSTED_WEIGHT = 1.0
DISCOUNTED_WEIGHT = 0.3


def _ensure_dir(axis):
    if axis not in AXES:
        raise ValueError(f"unknown axis '{axis}', must be one of {AXES}")
    os.makedirs(os.path.join(WEIGHTS_ROOT, axis), exist_ok=True)


def weights_path_for(axis):
    return os.path.join(WEIGHTS_ROOT, axis, "weights.json")


def log_path_for(axis):
    return os.path.join(WEIGHTS_ROOT, axis, "feedback_log.jsonl")


def load_weights(axis):
    """axis의 서브축별 현재 가중치 dict를 반환. 상태 파일이 없으면 전부 신뢰(1.0)로 시작."""
    path = weights_path_for(axis)
    if not os.path.exists(path):
        return {"promotion_threshold": 3, "sub_axes": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(axis, data):
    _ensure_dir(axis)
    with open(weights_path_for(axis), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def weight_for(axis, sub_axis):
    """subrubric.py가 채점 시 호출 — 상태 파일에 없는 서브축은 기본 TRUSTED_WEIGHT."""
    data = load_weights(axis)
    entry = data["sub_axes"].get(sub_axis)
    return entry["weight"] if entry else TRUSTED_WEIGHT


def record_feedback(axis, sub_axis, verdict, note="", source_finding=""):
    """verdict: "aligned"(서브축 값이 최종 판정과 잘 맞았다) | "misaligned"(안 맞았다).

    idiom_hook과 동일하게 즉시 weights.json을 바꾸지 않는다 — 조정은 recursive_update()에서만.
    """
    _ensure_dir(axis)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "axis": axis,
        "sub_axis": sub_axis,
        "verdict": verdict,
        "note": note,
        "source_finding": source_finding,
    }
    with open(log_path_for(axis), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recursive_update(axis):
    """로그를 재집계해 misaligned 독립 출처가 threshold 이상인 sub_axis를 discounted(0.3)로,
    미만이면 trusted(1.0)로 되돌린다 — 양방향(D50~D52와 동일한 수정 적용).

    D51/D52(isolation_hook.py/reflection_hook.py)와 동일한 두 가지 수정을 그대로 반영:
    1. source_finding(없으면 timestamp)으로 dedup — 같은 출처가 같은 sub_axis에 반복
       제출해도 1표만 인정한다(단일 사례로 threshold를 인위적으로 넘기는 것 방지).
    2. 재계산할 때마다 상태를 양방향으로 갱신한다 — "한 번 discounted면 영원히 discounted"가
       아니라, dedup 적용 후 독립 출처가 threshold 미만으로 드러나면 trusted로 되돌아간다.
       (aligned 표는 여전히 게이팅에 안 쓰고 감사 기록으로만 남긴다 — misaligned 쪽만
       dedup된 독립 출처 수로 판정)
    """
    _ensure_dir(axis)
    data = load_weights(axis)
    threshold = data["promotion_threshold"]
    by_key = dict(data["sub_axes"])

    log_path = log_path_for(axis)
    if not os.path.exists(log_path):
        return data, [], []

    misaligned_sources, aligned_sources = {}, {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            key = entry["sub_axis"]
            source_key = entry.get("source_finding") or entry["timestamp"]
            if entry["verdict"] == "misaligned":
                misaligned_sources.setdefault(key, set()).add(source_key)
            elif entry["verdict"] == "aligned":
                aligned_sources.setdefault(key, set()).add(source_key)

    all_keys = set(misaligned_sources) | set(aligned_sources)
    downgrades, upgrades = [], []
    for key in all_keys:
        if key not in by_key:
            by_key[key] = {"status": "trusted", "weight": TRUSTED_WEIGHT}
        entry = by_key[key]
        misaligned_count = len(misaligned_sources.get(key, set()))
        entry["misaligned_count"] = misaligned_count
        entry["aligned_count"] = len(aligned_sources.get(key, set()))
        entry["sources"] = sorted(misaligned_sources.get(key, set()))
        was_discounted = entry["status"] == "discounted"
        entry["status"] = "discounted" if misaligned_count >= threshold else "trusted"
        entry["weight"] = DISCOUNTED_WEIGHT if entry["status"] == "discounted" else TRUSTED_WEIGHT
        if not was_discounted and entry["status"] == "discounted":
            downgrades.append(key)
        elif was_discounted and entry["status"] == "trusted":
            upgrades.append(key)

    data["sub_axes"] = by_key
    _save(axis, data)
    return data, downgrades, upgrades


def main():
    if len(sys.argv) < 2:
        print("usage: subrubric_hook.py feedback <axis> <sub_axis> aligned|misaligned [note] [source_finding]", file=sys.stderr)
        print("       subrubric_hook.py update <axis>", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "feedback":
        axis, sub_axis, verdict = sys.argv[2], sys.argv[3], sys.argv[4]
        note = sys.argv[5] if len(sys.argv) > 5 else ""
        source_finding = sys.argv[6] if len(sys.argv) > 6 else ""
        print(json.dumps(record_feedback(axis, sub_axis, verdict, note, source_finding), ensure_ascii=False, indent=2))
    elif cmd == "update":
        axis = sys.argv[2]
        data, downgrades, upgrades = recursive_update(axis)
        print(json.dumps({"axis": axis, "downgrades": downgrades, "upgrades": upgrades, "sub_axes": data["sub_axes"]}, ensure_ascii=False, indent=2))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
