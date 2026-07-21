import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation_hook import SUPPORTED_CATEGORIES, load_patterns  # noqa: E402

# D40: cognition-isolation finding에 대한 답변이 "타당한 근거"인지 카테고리 매치로 판정
#   WHY: D19의 고립 판정 규칙이 구조 신호만으로는 다중 concern 코드베이스에서 과탐지를
#        일으킴(D38). 답변에 confirmed 카테고리 패턴이 하나라도 매치되면 "타당한 설계
#        근거가 있다"로 보고 우선순위를 낮출 근거로 쓴다 — idiom_filter의
#        question_value 하향과 같은 역할을 하지만 코드 신호가 아니라 자연어 답변이 트리거
#   COST: 카테고리가 confirmed되기 전(candidate 단계)에는 아무리 타당해 보이는 답변도
#        "미확정"으로 남는다 — D6/D32와 동일하게 성급한 신뢰보다 보수성을 우선함
#   EXIT: 여러 카테고리가 동시에 매치될 수 있음 — 지금은 "하나라도 매치=justified"로
#        단순하게 처리하지만, 나중에 카테고리별 강도(가중치)가 필요해지면 matched_categories
#        개수/종류로 세분화 가능


def detect_category(text, category):
    """confirmed 상태인 패턴만으로 텍스트를 검사한다 — candidate 패턴은 신뢰 안 함(D6 계승)."""
    data = load_patterns(category)
    for p in data["patterns"]:
        if p["status"] != "confirmed":
            continue
        if re.search(p["regex"], text):
            return True, p["id"]
    return False, None


def classify_justification(text):
    """4개 카테고리 각각을 confirmed 패턴으로 검사, 하나라도 매치하면 justified=True."""
    results = {}
    for cat in SUPPORTED_CATEGORIES:
        matched, pattern_id = detect_category(text, cat)
        results[cat] = {"matched": matched, "pattern_id": pattern_id}
    justified = any(r["matched"] for r in results.values())
    matched_categories = [c for c, r in results.items() if r["matched"]]
    return {"justified": justified, "matched_categories": matched_categories, "categories": results}


def main():
    if len(sys.argv) < 2:
        print("usage: isolation_classifier.py \"<answer text>\"", file=sys.stderr)
        sys.exit(1)
    import json
    print(json.dumps(classify_justification(sys.argv[1]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
