import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reflection_hook import SUB_SIGNALS, load_patterns  # noqa: E402

# D34: AND-4를 REQUIRED-1 + OR-3으로 완화 (self_error_recognition만 필수)
#   WHY: D33의 AND-4 실측 결과 B안 자체 모범 예시조차 탈락해 너무 엄격했음. 단순 OR-3(4개
#        중 아무 3개)으로 완화하면 자기오류인식 없이 "이유+새판단+개선안"만 있는 원래부터
#        맞는 답변(정답을 처음부터 알고 있었던 경우)까지 reflection으로 오판할 위험이 실측
#        프로브(아래 "그래서 백엔드에서 제한해야 합니다." 케이스)로 확인됨 — self_error_
#        recognition이 빠지면 "자기 수정"이라는 Reflection의 정의 자체가 성립 안 하므로
#        이것만은 OR로 완화하면 안 되고 항상 필수로 남겨야 한다
#   COST: self_error_recognition confirmed 패턴이 아직 1개뿐이라 다양한 오류인식 표현
#        ("아차", "잘못 봤네요", "정정하겠습니다" 등)을 못 잡으면 여전히 과소탐지 위험 있음
#   EXIT: 실제 학생 답변 데이터가 쌓이면 REQUIRED 서브신호를 더 늘리거나(예:
#        concrete_improvement도 필수) MIN_OPTIONAL_MATCHES 상수만 조정하면 재보정 가능
REQUIRED_SUB_SIGNALS = ("self_error_recognition",)
OPTIONAL_SUB_SIGNALS = tuple(s for s in SUB_SIGNALS if s not in REQUIRED_SUB_SIGNALS)
MIN_OPTIONAL_MATCHES = 2  # OPTIONAL 3개(reason/judgment/improvement) 중 최소 2개


def detect_sub_signal(text, sub_signal):
    """confirmed 상태인 정규식 패턴만으로 텍스트를 검사한다 — candidate 패턴은 신뢰 안 함(D6 계승)."""
    data = load_patterns(sub_signal)
    for p in data["patterns"]:
        if p["status"] != "confirmed":
            continue
        if re.search(p["regex"], text):
            return True, p["id"]
    return False, None


def evaluate_reflection(text):
    """self_error_recognition은 필수, 나머지 3개 중 MIN_OPTIONAL_MATCHES개 이상이면 True(D34).

    순수 OR-3(4개 중 아무 3개)은 자기오류인식 없이도 통과 가능해 "원래부터 맞았던 답변"을
    reflection으로 오판할 위험이 실측으로 확인돼(아래 probe) 채택하지 않았다.
    """
    results = {}
    for sub in SUB_SIGNALS:
        matched, pattern_id = detect_sub_signal(text, sub)
        results[sub] = {"matched": matched, "pattern_id": pattern_id}

    required_ok = all(results[s]["matched"] for s in REQUIRED_SUB_SIGNALS)
    optional_matches = sum(1 for s in OPTIONAL_SUB_SIGNALS if results[s]["matched"])
    reflection_present = required_ok and optional_matches >= MIN_OPTIONAL_MATCHES

    matched_count = sum(1 for r in results.values() if r["matched"])
    return {
        "reflection_present": reflection_present,
        "matched_count": matched_count,
        "total_sub_signals": len(SUB_SIGNALS),
        "required_ok": required_ok,
        "optional_matches": optional_matches,
        "min_optional_required": MIN_OPTIONAL_MATCHES,
        "sub_signals": results,
    }


def main():
    if len(sys.argv) < 2:
        print("usage: reflection_signal.py \"<text>\"", file=sys.stderr)
        sys.exit(1)
    import json
    print(json.dumps(evaluate_reflection(sys.argv[1]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
