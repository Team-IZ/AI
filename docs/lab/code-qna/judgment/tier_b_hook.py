import json
import os
import sys
from datetime import datetime, timezone

# D14: Tier B 오탐 억제를 인지 블록이 아니라 판단 블록에 위치시킴
#   WHY: "이 매치가 진짜 위험인지 오탐인지"는 사실 추출이 아니라 신뢰도 판단이다 —
#        idiom_filter.py가 관용패턴 신뢰도를 다루는 것과 동일한 성격의 문제라
#        같은 계층(판단)에 두는 게 3블록 경계를 지킨다. 애초에 "인지 블록용 훅"으로
#        요청받았지만 구현하며 정확한 위치를 재확인했다.
#   COST: 인지 블록(cognition/two_tier_scan.py)은 오탐 여부를 전혀 모른 채 raw match를
#        그대로 내보내야 함 — 인지 블록 출력만 보면 여전히 오탐이 섞여 있는 것처럼 보임(의도된 설계)
#   EXIT: 만약 판단 블록 분리가 과했다고 판명되면 이 파일을 cognition/으로 옮기고
#        import 경로만 바꾸면 됨(로직 자체는 이동 무관)
#
# 재귀 루프 구조(idiom_hook.py와 동일 패턴):
#   score_findings.py 실행 → (사람이) Tier B 히트를 "오탐이었다"고 판정
#   → record_false_positive()로 feedback_log.jsonl에 적재
#   → recursive_update() → 같은 (trigger, matched_text)가 threshold 이상 쌓이면
#     candidate→confirmed 승격 → 다음 스캔부터 tier_b_suppression_filter.py가 자동 제외

SUPPRESSIONS_DIR = os.path.join(os.path.dirname(__file__), "tier_b_suppressions")
SUPPRESSIONS_PATH = os.path.join(SUPPRESSIONS_DIR, "suppressions.json")
LOG_PATH = os.path.join(SUPPRESSIONS_DIR, "feedback_log.jsonl")


def _load():
    if not os.path.exists(SUPPRESSIONS_PATH):
        return {"promotion_threshold": 3, "suppressions": []}
    with open(SUPPRESSIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    os.makedirs(SUPPRESSIONS_DIR, exist_ok=True)
    with open(SUPPRESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def record_false_positive(trigger, matched_text, note=""):
    os.makedirs(SUPPRESSIONS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "matched_text": matched_text,
        "note": note,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recursive_update():
    data = _load()
    threshold = data["promotion_threshold"]
    by_key = {(s["trigger"], s["matched_text"]): s for s in data["suppressions"]}

    if not os.path.exists(LOG_PATH):
        return data, []

    votes = {}
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            key = (entry["trigger"], entry["matched_text"])
            votes[key] = votes.get(key, 0) + 1

    promotions = []
    for key, count in votes.items():
        trigger, matched_text = key
        if key not in by_key:
            by_key[key] = {"trigger": trigger, "matched_text": matched_text, "status": "candidate", "confirmations": 0}
        entry = by_key[key]
        entry["confirmations"] = count
        if entry["status"] != "confirmed" and count >= threshold:
            entry["status"] = "confirmed"
            promotions.append(key)

    data["suppressions"] = list(by_key.values())
    _save(data)
    return data, promotions


def confirmed_keys():
    data = _load()
    return {(s["trigger"], s["matched_text"]) for s in data["suppressions"] if s["status"] == "confirmed"}


def main():
    if len(sys.argv) < 2:
        print("usage: tier_b_hook.py feedback <trigger> <matched_text> [note]", file=sys.stderr)
        print("       tier_b_hook.py update", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "feedback":
        trigger, matched_text = sys.argv[2], sys.argv[3]
        note = sys.argv[4] if len(sys.argv) > 4 else ""
        print(json.dumps(record_false_positive(trigger, matched_text, note), ensure_ascii=False, indent=2))
    elif cmd == "update":
        data, promotions = recursive_update()
        print(json.dumps({"promotions": promotions, "suppressions": data["suppressions"]}, ensure_ascii=False, indent=2))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
