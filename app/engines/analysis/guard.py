""" 질문·힌트에 선택지가 섞이는 것을 막는다. question-guard.js 포팅.

**실측 사고(2026-07-28)**: 질문 본문에 보기 3개가 들어갔고, 학생이 "저는 3번"이라고
답해 대안 비교 축에서 5점을 받았다. 대안을 제시한 게 아니라 고른 것이다.

graduated prompting에서 "선택지 제시"는 힌트 사다리의 가장 강한 단계다. 그게 질문에
무심코 섞이면 **모든 학생이 매 문제 최강 힌트를 공짜로 받는 셈**이라 자력/보조 구분이
통째로 무너진다.

프롬프트로 "선택지 넣지 마라"고 이미 지시하고 있다(p04-4). 그런데 어긴 사례가 실측으로
확인됐으므로 **생성물을 신뢰하지 않고 사후 검사한다.**

정규식은 근사치다 — 오탐(정상 문장을 걸러냄)과 미탐(교묘한 선택지 통과) 둘 다 가능하다.
오탐은 재생성 비용으로 끝나지만 미탐은 위 사고가 재발한다. 재생성이 상한을 넘으면
`flagged`로 남겨 **사람이 보게 한다 — 조용히 통과시키지 않는다.**

**원본에서 고친 것 2가지** (팀원 원본도 고쳐야 실측이 같아진다):
  · `[A-DA-D]` → `[A-Da-d]`  (오타로 보인다. 의도는 대소문자였을 것)
  · "A와 B 중" 패턴이 각 항을 한 어절로만 봐서 `"동기 방식과 비동기 방식 중 왜 이것을
    골랐나요?"`를 통과시켰다 — 최대 3어절까지 허용하도록 넓혔다.
"""

import re

_WORD = r"[A-Za-z가-힣0-9]+"

_PATTERNS = [
    # 원문자 열거: ①②③
    re.compile(r"[①②③④⑤]"),
    # 괄호 번호가 2개 이상: (1) ... (2)
    re.compile(r"\([1-5]\)\s*\S+.*\([1-5]\)", re.S),
    # 줄머리 번호 매김이 2개 이상. 단답 예시 하나("1) 이렇게 하면...")는 통과시키고
    # 목록으로 나열된 경우만 잡기 위해 최소 2회를 요구한다.
    re.compile(r"(^|\n)\s*[1-5][).]\s+\S+[\s\S]*(^|\n)\s*[1-5][).]\s+\S+", re.M),
    re.compile(r"(^|\n)\s*[A-Da-d][).]\s+\S+[\s\S]*(^|\n)\s*[A-Da-d][).]\s+\S+", re.M),
    # "A와 B 중", "다음 중", "보기 중에서" 류 지시어.
    # 원본은 A·B를 한 어절로만 봐서 "동기 방식과 비동기 방식 중"을 놓쳤다(실측).
    # 각 항을 최대 3어절까지 허용해 막는다 — 오탐은 재생성 비용으로 끝나지만
    # 미탐은 D-poc7 사고가 재발한다.
    re.compile(rf"{_WORD}(?:\s+{_WORD}){{0,2}}\s*(?:와|과)\s*{_WORD}(?:\s+{_WORD}){{0,2}}\s*중"),
    re.compile(r"다음\s*중"),
    re.compile(r"보기\s*(중|에서)"),
]


def check(text: str) -> list[str]:
    """위반한 부분들을 돌려준다. 빈 리스트면 통과."""
    s = str(text or "")
    return [m.group(0)[:40] for m in (p.search(s) for p in _PATTERNS) if m]


def check_levels(levels: list[dict]) -> list[dict[str, str]]:
    """질문 세트 전체(질문 + 힌트)를 검사. [{axis, field, matched}]."""
    violations = []
    for level in levels or []:
        matched = check(level.get("question"))
        if matched:
            violations.append({"axis": level.get("axis", "?"), "field": "question",
                               "matched": ", ".join(matched)})
        for hint in level.get("hints") or []:
            matched = check(hint.get("text"))
            if matched:
                violations.append({"axis": level.get("axis", "?"),
                                   "field": f"hint(lv{hint.get('lv')})",
                                   "matched": ", ".join(matched)})
    return violations