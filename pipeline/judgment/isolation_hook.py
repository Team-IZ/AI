import json
import os
import sys
from datetime import datetime, timezone

# D39: cognition-isolation 판정에 idiom_hook 아이디어를 적용하되, 패턴 단위를
#   "정규식 하나"가 아니라 "카테고리 하나(동의어 alternation 정규식)"로 바꿈
#   WHY: D38 실측 — 6명의 Codex 가상 학생이 GenreContext 미사용을 각자 다른 표현으로
#        정당화함(위임/성능/대체저장소/도메인무관). idiom_hook처럼 "정규식 하나=패턴 하나"로
#        설계하면 reflection_hook이 이미 실증한 것과 같은 재현율 붕괴가 그대로 재발함
#        (Codex의 좋은 reflection 답변도 confirmed 패턴 문구와 안 맞아 0/4로 놓쳤음, D37).
#        대신 4개 카테고리(role_separation/perf_optimization/alt_storage_or_scope/
#        domain_irrelevance)를 미리 정의하고, 카테고리당 "관측된 동의어들의 alternation"
#        하나를 패턴으로 축적 — 사람이 위임을 "위임한다"/"맡긴다"/"책임을 나눈다" 등
#        다양하게 표현해도 같은 카테고리 패턴 하나가 넓게 커버하게 한다
#   COST: 카테고리 경계를 사람이 미리 정해야 함(코드처럼 유한한 문법이 아니라 자연어라
#        새로운 정당화 유형이 나오면 카테고리 자체를 추가해야 함 — idiom_hook은 패턴만
#        추가하면 됐지만 여긴 분류체계 자체가 열려있어야 함)
#   EXIT: 카테고리가 너무 좁아 자주 "미분류"가 나오면 카테고리를 추가(SUPPORTED_CATEGORIES에
#        새 이름 추가 + 디렉토리 생성)하거나, 최후 수단으로 LLM에 자유분류를 맡기고
#        그 결과를 여기 카테고리 중 하나로 매핑하는 반자동 모드로 전환

ISOLATION_HOOK_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "isolation_categories")
SUPPORTED_CATEGORIES = ("role_separation", "perf_optimization", "alt_storage_or_scope", "domain_irrelevance")


def _ensure_dir(category):
    if category not in SUPPORTED_CATEGORIES:
        raise ValueError(f"unknown category '{category}', must be one of {SUPPORTED_CATEGORIES}")
    os.makedirs(os.path.join(ISOLATION_HOOK_ROOT, category), exist_ok=True)


def patterns_path_for(category):
    return os.path.join(ISOLATION_HOOK_ROOT, category, "patterns.json")


def log_path_for(category):
    return os.path.join(ISOLATION_HOOK_ROOT, category, "feedback_log.jsonl")


def load_patterns(category):
    path = patterns_path_for(category)
    if not os.path.exists(path):
        return {"promotion_threshold": 3, "patterns": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(category, data):
    _ensure_dir(category)
    with open(patterns_path_for(category), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def record_feedback(category, pattern_id, regex, verdict, note="", source_finding=""):
    """verdict: "genuine_justification"(진짜 타당한 근거) | "not_justified"(핑계/방치).

    idiom_hook과 동일하게 즉시 patterns.json을 바꾸지 않는다 — 승격은 recursive_update()에서만.
    """
    _ensure_dir(category)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "pattern_id": pattern_id,
        "regex": regex,
        "verdict": verdict,
        "note": note,
        "source_finding": source_finding,
    }
    with open(log_path_for(category), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recursive_update(category):
    """로그를 재집계해 threshold 이상 confirm된 pattern_id를 candidate→confirmed로 승격."""
    _ensure_dir(category)
    data = load_patterns(category)
    threshold = data["promotion_threshold"]
    by_id = {p["id"]: p for p in data["patterns"]}

    log_path = log_path_for(category)
    if not os.path.exists(log_path):
        return data, []

    regex_map, sources = {}, {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry["verdict"] != "genuine_justification":
                continue
            key = entry["pattern_id"]
            regex_map[key] = entry["regex"]
            # D51: source_finding이 같으면 반복 제출해도 1표만 인정(단일 사례로 threshold 우회 방지)
            sources.setdefault(key, set()).add(entry.get("source_finding") or entry["timestamp"])

    # D51: 재계산할 때마다 상태를 양방향 갱신(승격뿐 아니라 강등도) — dedup 적용 후
    #   독립 출처가 threshold 미만으로 드러나면 confirmed로 남겨두지 않는다
    promotions, demotions = [], []
    for key, src_set in sources.items():
        count = len(src_set)
        if key not in by_id:
            by_id[key] = {"id": key, "regex": regex_map[key], "status": "candidate", "confirmations": 0, "sources": []}
        entry = by_id[key]
        entry["confirmations"] = count
        entry["sources"] = sorted(src_set)
        was_confirmed = entry["status"] == "confirmed"
        entry["status"] = "confirmed" if count >= threshold else "candidate"
        if not was_confirmed and entry["status"] == "confirmed":
            promotions.append(key)
        elif was_confirmed and entry["status"] == "candidate":
            demotions.append(key)

    data["patterns"] = list(by_id.values())
    _save(category, data)
    return data, promotions, demotions


def main():
    if len(sys.argv) < 2:
        print("usage: isolation_hook.py feedback <category> <pattern_id> <regex> genuine_justification|not_justified [note] [source_finding]", file=sys.stderr)
        print("       isolation_hook.py update <category>", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "feedback":
        category, pattern_id, regex, verdict = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        note = sys.argv[6] if len(sys.argv) > 6 else ""
        source_finding = sys.argv[7] if len(sys.argv) > 7 else ""
        print(json.dumps(record_feedback(category, pattern_id, regex, verdict, note, source_finding), ensure_ascii=False, indent=2))
    elif cmd == "update":
        category = sys.argv[2]
        data, promotions, demotions = recursive_update(category)
        print(json.dumps({"category": category, "promotions": promotions, "demotions": demotions, "patterns": data["patterns"]}, ensure_ascii=False, indent=2))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
