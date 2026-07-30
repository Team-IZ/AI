""" app/engines/shared/evidence.py 단위 테스트

D1 (2026-07-30): evidence_hash/스니펫 슬라이싱은 여러 엔진 스테이지가 공유하는
유일한 구현이어야 한다 -- 스테이지마다 따로 계산하면 줄바꿈 문자 등 사소한 차이로
같은 코드인데 해시가 어긋나고, Spring의 무결성 검사가 조용히 실패한다.
"""
from app.engines.shared.evidence import evidence_hash, locate_symbol, slice_snippet


def test_hash_is_64_hex():
    h = evidence_hash("const x = 1;\n")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_is_stable_across_line_endings():
    lf = "line1\nline2\n"
    crlf = "line1\r\nline2\r\n"
    cr = "line1\rline2\r"
    assert evidence_hash(lf) == evidence_hash(crlf) == evidence_hash(cr)


def test_hash_is_stable_regardless_of_trailing_newline_presence():
    assert evidence_hash("line1\nline2") == evidence_hash("line1\nline2\n")


def test_hash_differs_for_different_content():
    assert evidence_hash("a\n") != evidence_hash("b\n")


def test_slice_is_1_indexed_inclusive():
    text = "one\ntwo\nthree\nfour\n"
    assert slice_snippet(text, 2, 3) == "two\nthree"


def test_slice_rejects_invalid_range():
    import pytest

    with pytest.raises(ValueError):
        slice_snippet("a\nb\n", 3, 2)
    with pytest.raises(ValueError):
        slice_snippet("a\nb\n", 0, 1)


def test_locate_symbol_finds_exact():
    text = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
    loc = locate_symbol(text, "def bar():")
    assert loc == (5, 6)


def test_locate_symbol_normalizes_whitespace():
    text = "def   foo():\n    return 1\n"
    loc = locate_symbol(text, "def foo():")
    assert loc == (1, 2)


def test_locate_symbol_returns_none_when_absent():
    text = "def foo():\n    return 1\n"
    assert locate_symbol(text, "def nonexistent():") is None


def test_locate_symbol_stops_at_dedent():
    text = (
        "def foo():\n"
        "    x = 1\n"
        "    y = 2\n"
        "\n"
        "def bar():\n"
        "    z = 3\n"
    )
    start, end = locate_symbol(text, "def foo():")
    assert start == 1
    # blank line(4) is swallowed as part of the block; bar()가 나오는 5행에서 멈춘다
    assert end == 4
