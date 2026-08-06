""" cursor.mac 무결성 회귀 테스트 (2026-08-04, redteam audit H10).

_seek()이 "커서가 오면 그대로 믿는다"였던 걸, mac이 있고 서버가 검증 가능하면(secret
설정됨) 먼저 대조하도록 바꿨다. 이 파일은 (a) secret 미설정이면 오늘처럼 무검증으로
동작하는지(하위호환), (b) secret 설정 시 정상 왕복은 계속 통과하는지, (c) cursor/
problems/transcript 중 하나라도 위조하면 거부되는지를 확인한다.
"""
import copy

import pytest

from app import sessions as sessions_mod
from app.config import get_settings
from test_sessions import PROBLEMS, Backend, score  # noqa: F401 (score = fixture)

TEST_SECRET = "test-only-hmac-secret-do-not-use-in-real-deployment"


@pytest.fixture
def hmac_secret(monkeypatch):
    """이 테스트 동안만 session_cursor_hmac_secret을 설정한다."""
    monkeypatch.setattr(get_settings(), "session_cursor_hmac_secret", TEST_SECRET)
    return TEST_SECRET


def test_without_secret_configured_mac_is_absent_and_trusted_as_before(score):
    """secret 미설정(기본/오늘) -- mac 필드가 없거나 None이고, 검증도 스킵된다(하위호환)."""
    body = Backend().answer()
    assert body["cursor"]["mac"] is None


def test_with_secret_configured_mac_is_populated(hmac_secret, score):
    body = Backend().answer()
    assert body["cursor"]["mac"]
    assert isinstance(body["cursor"]["mac"], str)
    assert len(body["cursor"]["mac"]) == 64  # sha256 hexdigest


def test_legitimate_round_trip_still_works_with_mac_enabled(hmac_secret, score):
    """정상적으로 응답의 cursor/transcript를 그대로 되돌려 보내는 흐름은 안 깨져야 한다."""
    session = Backend()
    for _ in range(4):  # L1~L4를 정상 통과
        body = session.answer()
    assert body["state"] in ("IN_PROGRESS", "COMPLETED")
    assert body["cursor"] is None or "mac" in body["cursor"]


def test_tampered_hints_used_is_rejected(hmac_secret, score):
    """cursor.hintsUsed를 위조하면(mac은 그대로) 거부된다."""
    session = Backend()
    session.answer()
    tampered = copy.deepcopy(session.cursor)
    tampered["hintsUsed"] = 2  # 실제로 힌트를 안 받고도 상한만큼 받은 것처럼 위조
    resp = session.answer(cursor=tampered)
    assert resp.get("error") == "CURSOR_INTEGRITY_MISMATCH"


def test_tampered_axis_code_is_rejected(hmac_secret, score):
    """cursor.axisCode를 앞으로 건너뛰게 위조하면(mac은 그대로) 거부된다."""
    session = Backend()
    session.answer()
    tampered = copy.deepcopy(session.cursor)
    tampered["axisCode"] = "L4"  # L1 통과 없이 곧바로 L4로 건너뛰기 시도
    resp = session.answer(cursor=tampered)
    assert resp.get("error") == "CURSOR_INTEGRITY_MISMATCH"


def test_tampered_problems_is_rejected_even_with_valid_cursor(hmac_secret, score):
    """cursor 자체는 안 건드리고 problems(질문 내용)만 바꿔도 mac이 안 맞아 거부된다 --
    problems 해시가 서명 입력에 포함돼 있어야만 잡히는, cursor단독서명이면 놓치는 경로."""
    session = Backend()
    session.answer()
    tampered_problems = copy.deepcopy(session.problems)
    tampered_problems[0]["stages"][1]["questionText"] = "훨씬 쉬운 질문으로 바꿔치기"
    resp = session.answer(problems=tampered_problems)
    assert resp.get("error") == "CURSOR_INTEGRITY_MISMATCH"


def test_tampered_transcript_is_rejected_even_with_valid_cursor(hmac_secret, score):
    """transcript(과거 턴의 점수/통과여부)를 위조해도 mac이 안 맞아 거부된다 -- 마찬가지로
    cursor단독서명이면 놓치는 경로."""
    session = Backend()
    session.answer()
    tampered_transcript = copy.deepcopy(session.transcript)
    tampered_transcript[0]["score"] = 5
    tampered_transcript[0]["passed"] = True
    resp = session.answer(transcript=tampered_transcript)
    assert resp.get("error") == "CURSOR_INTEGRITY_MISMATCH"


def test_omitting_mac_entirely_still_works_mid_migration(hmac_secret, score):
    """secret이 설정돼 있어도, 클라이언트가 mac 필드를 안 보내면(마이그레이션 중인 호출자)
    거부하지 않고 오늘처럼 신뢰한다 -- 강제 전환이 아니라 점진적 도입이라는 설계 그대로."""
    session = Backend()
    session.answer()
    cursor_without_mac = {k: v for k, v in session.cursor.items() if k != "mac"}
    resp = session.answer(cursor=cursor_without_mac)
    assert "error" not in resp or resp.get("error") != "CURSOR_INTEGRITY_MISMATCH"
