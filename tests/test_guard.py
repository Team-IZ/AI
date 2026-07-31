""" 선택지 금지 검사(T7b). 여기 뚫리면 자력/보조 구분이 무너진다. """
import pytest

from app.engines.analysis import guard


@pytest.mark.parametrize("text", [
    "이 설계의 대안은 ① 캐시 ② 인덱스 ③ 비정규화 중 무엇인가요?",
    "다음 중 이 코드의 문제는 무엇인가요?",
    "보기 중에서 골라 설명해보세요",
    "동기 방식과 비동기 방식 중 왜 이것을 골랐나요?",
    "1) 캐시를 쓴다\n2) 인덱스를 만든다\n어느 쪽인가요?",
    "A) 락을 건다\nB) 큐를 쓴다\n무엇이 나은가요?",
])
def test_choices_are_caught(text):
    """실측 사고 재현 — 선택지가 섞이면 학생이 '고르기'로 만점을 받는다."""
    assert guard.check(text)
""" 선택지 금지 검사(T7b). 여기 뚫리면 자력/보조 구분이 무너진다. """
import pytest

from app.engines.analysis import guard


@pytest.mark.parametrize("text", [
    "이 설계의 대안은 ① 캐시 ② 인덱스 ③ 비정규화 중 무엇인가요?",
    "다음 중 이 코드의 문제는 무엇인가요?",
    "보기 중에서 골라 설명해보세요",
    "동기 방식과 비동기 방식 중 왜 이것을 골랐나요?",
    "1) 캐시를 쓴다\n2) 인덱스를 만든다\n어느 쪽인가요?",
    "A) 락을 건다\nB) 큐를 쓴다\n무엇이 나은가요?",
])
def test_choices_are_caught(text):
    """실측 사고 재현 — 선택지가 섞이면 학생이 '고르기'로 만점을 받는다."""
    assert guard.check(text)


@pytest.mark.parametrize("text", [
    "이 함수가 무엇을 하는지 데이터 흐름을 따라 설명해보세요.",
    "왜 이 구조를 선택했나요? 어떤 제약이 있었는지 알려주세요.",
    "1) 처럼 단일 예시를 드는 문장은 선택지가 아닙니다.",
    "이 설계가 깨지는 조건이 있다면 무엇인가요?",
    # "와"는 있지만 "중"이 없다. 정규식을 3어절까지 넓혔으므로 경계를 못 박아둔다.
    "이 함수와 저 함수의 호출 순서를 설명해보세요.",
])
def test_normal_questions_pass(text):
    """오탐은 재생성 비용이다. 정상 질문을 막으면 안 된다."""
    assert guard.check(text) == []


def test_hints_are_checked_too():
    """힌트에 선택지가 들어가면 사다리 3단계를 공짜로 주는 셈이다."""
    levels = [{"axis": "L1_코드기술", "question": "무엇을 하나요?",
               "hints": [{"lv": 1, "text": "다음 중 어느 쪽인가요?"}]}]

    v = guard.check_levels(levels)

    assert len(v) == 1
    assert v[0]["field"] == "hint(lv1)"