""" openapi.json이 코드와 어긋나지 않는지.

**백엔드가 이 파일 하나로 구현한다.** 스펙이 낡으면 백엔드는 옛 계약대로 짜고,
그 사실은 통합 시점에야 드러난다 — 그때는 이미 양쪽 다 고쳐야 한다.
지금까지 재생성이 사람 손이었고 실제로 세 번 낡았다(T5 1차·3차·오늘).
"""
import json
from pathlib import Path

from app.main import app

SPEC = Path(__file__).resolve().parent.parent / "openapi.json"


def _spec() -> dict:
    return json.loads(SPEC.read_text(encoding="utf-8"))


def test_spec_matches_the_code():
    """계약을 바꾸고 재생성을 안 하면 여기서 깨진다.

    고치는 법: `python tools/dump_openapi.py`
    """
    assert json.dumps(_spec(), sort_keys=True) == json.dumps(app.openapi(), sort_keys=True), (
        "openapi.json이 낡았습니다. `python tools/dump_openapi.py`를 돌리세요."
    )


def test_every_endpoint_is_published():
    """스펙에 없는 엔드포인트는 백엔드에게 존재하지 않는 것과 같다."""
    published = set(_spec()["paths"])

    assert "/api/health" in published
    for path in ("/api/v0/analyses", "/api/v0/sessions", "/api/v0/reports",
                 "/api/v0/curricula"):
        assert path in published, path
    assert len(published) == 11


def test_frozen_contract_is_visible_in_the_spec():
    """2026-08-02 계약 변경 4건이 스펙 표면에 나와야 한다.

    OpenAPI로 표현 안 되는 규칙(축별 힌트 2개 강제 등)은 산문으로 따로 전달한다
    — tools/dump_openapi.py 주석에 목록이 있다.
    """
    schemas = _spec()["components"]["schemas"]

    def props(name):
        return schemas[name]["properties"]

    # ① 보고서는 문제 단위다
    assert "problemId" in props("ReportRequest")
    assert "problem" in props("ReportResult") and "problems" not in props("ReportResult")

    # ② 총점을 만들지 않는다 (자리를 만들면 누군가 채운다)
    assert "totalScore" not in props("ProblemResult")
    assert "maxScore" not in props("ProblemResult")

    # ③ 도달 단계가 판정값이다
    assert props("ProblemResult")["reachedStage"]["maximum"] == 4

    # ④ teach 앵커 없는 문제는 표기된다
    assert "isGeneral" in props("Problem")

    # 전면 동결 — 세션은 저장분을 받아 쓰기만 한다
    assert "problems" in props("SessionStart")
    assert "hintText" in props("Question")


def test_grading_failure_is_documented():
    """503을 문서화하지 않으면 백엔드가 재전송 규칙을 모른다."""
    answers = _spec()["paths"]["/api/v0/sessions/{session_id}/answers"]["post"]

    assert "503" in answers["responses"]
    assert "clientRequestId" in answers["responses"]["503"]["description"]
