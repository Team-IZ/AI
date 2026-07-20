import json
import os
import sys
from datetime import datetime, timezone

# D32: Reflection 판정을 idiom_hook.py와 동일한 "재귀 확인 후 신뢰" 패턴으로 가드
#   WHY: POC_TEST.md 문제4 — "관용패턴 확정처럼 Reflection도 답변이 조금만 바뀌면 즉시
#        true가 되는" 문제(B안 POC 비평 재현). 자기오류인식→이유설명→새판단→개선안 4단계가
#        전부 확인돼야 진짜 Reflection인데, 정규식 하나가 우연히 매치했다고 즉시 신뢰하면
#        idiom_filter가 관용패턴 오탐으로 겪었던 것과 같은 문제가 재발함
#   COST: 서브신호별로 confirmed 패턴이 쌓이기 전까지는 reflection_present가 항상 False —
#        보수적이라 초기엔 진짜 reflection도 못 잡음(D6 원칙: 미확정 신호는 안 믿는다)
#   EXIT: sub_signal마다 별도 threshold를 두고 싶으면 patterns.json의
#        promotion_threshold를 sub_signal별로 다르게 설정하면 됨(이미 파일 분리돼 있어 가능)

REFLECTION_HOOK_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reflection_patterns")
SUB_SIGNALS = ("self_error_recognition", "reason_explanation", "new_judgment", "concrete_improvement")


def _ensure_dir(sub_signal):
    if sub_signal not in SUB_SIGNALS:
        raise ValueError(f"unknown sub_signal '{sub_signal}', must be one of {SUB_SIGNALS}")
    os.makedirs(os.path.join(REFLECTION_HOOK_ROOT, sub_signal), exist_ok=True)


def patterns_path_for(sub_signal):
    return os.path.join(REFLECTION_HOOK_ROOT, sub_signal, "patterns.json")


def log_path_for(sub_signal):
    return os.path.join(REFLECTION_HOOK_ROOT, sub_signal, "feedback_log.jsonl")


def load_patterns(sub_signal):
    path = patterns_path_for(sub_signal)
    if not os.path.exists(path):
        return {"promotion_threshold": 3, "patterns": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_load = load_patterns  # 내부 호출부와의 하위호환 별칭


def _save(sub_signal, data):
    _ensure_dir(sub_signal)
    with open(patterns_path_for(sub_signal), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def record_feedback(sub_signal, pattern_id, regex, verdict, note="", source_finding=""):
    """verdict: "genuine_signal"(진짜 이 서브신호를 나타냄) | "false_positive"(오탐).

    idiom_hook.py와 동일하게 즉시 patterns.json을 바꾸지 않는다 — 승격은 recursive_update()에서만.
    source_finding: 이 신호가 관측된 출처(finding_id). recursive_update()가 같은 출처의
    반복 제출을 중복 확인으로 세지 않기 위해 필요(D51).
    """
    _ensure_dir(sub_signal)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sub_signal": sub_signal,
        "pattern_id": pattern_id,
        "regex": regex,
        "verdict": verdict,
        "note": note,
        "source_finding": source_finding,
    }
    with open(log_path_for(sub_signal), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recursive_update(sub_signal):
    """로그 전체를 재집계해 threshold 이상 confirm된 pattern_id를 candidate→confirmed로 승격."""
    _ensure_dir(sub_signal)
    data = _load(sub_signal)
    threshold = data["promotion_threshold"]
    by_id = {p["id"]: p for p in data["patterns"]}

    log_path = log_path_for(sub_signal)
    if not os.path.exists(log_path):
        return data, []

    sources, regex_map = {}, {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry["verdict"] != "genuine_signal":
                continue
            key = entry["pattern_id"]
            # D51: 같은 source_finding이 같은 pattern_id를 반복 제출해도 1표만 인정 —
            #   그렇지 않으면 단일 사례를 여러 번 제출해 threshold를 인위적으로 넘길 수 있음
            sources.setdefault(key, set()).add(entry.get("source_finding") or entry["timestamp"])
            regex_map[key] = entry["regex"]

    # D51: 재계산할 때마다 상태를 양방향으로 갱신한다(승격뿐 아니라 강등도) — 과거에
    #   같은 출처를 반복 제출해 confirmed됐던 패턴(too_trusted_browser)이 dedup 적용 후
    #   실제 독립 출처가 threshold 미만으로 드러나면 그대로 confirmed로 남겨두지 않는다.
    #   "한 번 확정되면 영원히 확정"은 이 시스템 전체의 재귀 확인 철학(D6/D41)에 위배됨
    promotions, demotions = [], []
    for key, src_set in sources.items():
        count = len(src_set)
        if key not in by_id:
            by_id[key] = {"id": key, "regex": regex_map[key], "status": "candidate", "confirmations": 0}
        entry = by_id[key]
        entry["confirmations"] = count
        was_confirmed = entry["status"] == "confirmed"
        entry["status"] = "confirmed" if count >= threshold else "candidate"
        if not was_confirmed and entry["status"] == "confirmed":
            promotions.append(key)
        elif was_confirmed and entry["status"] == "candidate":
            demotions.append(key)

    data["patterns"] = list(by_id.values())
    _save(sub_signal, data)
    return data, promotions, demotions


def main():
    if len(sys.argv) < 2:
        print("usage: reflection_hook.py feedback <sub_signal> <pattern_id> <regex> genuine_signal|false_positive [note] [source_finding]", file=sys.stderr)
        print("       reflection_hook.py update <sub_signal>", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "feedback":
        sub_signal, pattern_id, regex, verdict = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        note = sys.argv[6] if len(sys.argv) > 6 else ""
        source_finding = sys.argv[7] if len(sys.argv) > 7 else ""
        print(json.dumps(record_feedback(sub_signal, pattern_id, regex, verdict, note, source_finding), ensure_ascii=False, indent=2))
    elif cmd == "update":
        sub_signal = sys.argv[2]
        data, promotions, demotions = recursive_update(sub_signal)
        print(json.dumps({"sub_signal": sub_signal, "promotions": promotions, "demotions": demotions, "patterns": data["patterns"]}, ensure_ascii=False, indent=2))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
