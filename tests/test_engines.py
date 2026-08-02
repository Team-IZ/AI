""" 5단계 엔진 소켓 테스트.

핵심은 test_router_uses_injected_engine: 의존성 오버라이드로 엔진을 갈아끼워도
라우터가 그걸 그대로 쓰는지 확인. 통과 = 9단계에서 라우터 안 건드리고 엔진 교체 가능.
"""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.engines import get_analysis_engine
from app.engines.stub import StubAnalysisEngine
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

VALID_BODY = {
    "method": "GITHUB_URL",
    "source": {"repoUrl": "https://github.com/owner/repo"},
    "extractionScope": "TOTAL",
    "questionBudget": 4,
}


def test_stub_returns_contract_shape():
    """스텁 결과가 AnalysisResult 최상위 계약 필드를 모두 갖는지."""
    raw = StubAnalysisEngine().analyze({"extraction_scope": "TOTAL", "question_budget": 4})

    assert raw["snapshot_id"]
    assert raw["applied_scope"] == "TOTAL"
    assert raw["scope_fallback"] is False
    assert raw["question_count_planned"] == 4


def test_stub_reflects_request_values():
    """요청 값이 결과에 흐르는지 — 배선 확인. scope와 예산이 그대로 반영."""
    raw = StubAnalysisEngine().analyze(
        {"extraction_scope": "OWN_COMMIT", "question_budget": 7},
        zip_bytes=b"1234567890",
    )

    assert raw["applied_scope"] == "OWN_COMMIT"
    assert raw["question_count_planned"] == 7
    assert raw["snapshot_meta"]["byte_count"] == 10  # len(zip_bytes)


def test_problems_use_db_columns():
    """problem이 DB 컬럼명(problem_id, source_path 등)을 쓰는지. 임의 이름이면 실패."""
    raw = StubAnalysisEngine().analyze({"extraction_scope": "TOTAL", "question_budget": 3})
    problem = raw["problems"][0]

    assert "problem_id" in problem
    assert "source_path" in problem
    assert problem["status"] == "READY"          # 4종. OPEN은 DB CHECK에 없다
    assert problem["references"][0]["reference_type"] == "CALLER"   # PRIMARY 폐기

    # 전면 동결 — 4단계 전부 분석 때 질문 1개 + 힌트 2개가 채워진다.
    assert [s["axis_code"] for s in problem["stages"]] == ["L1", "L2", "L3", "L4"]
    assert all(s["question_text"] and len(s["hints"]) == 2 for s in problem["stages"])


def test_real_mode_returns_the_real_engine(monkeypatch):
    """engine_mode=real이면 실물 엔진이 나온다. 스텁으로 조용히 폴백하지 않는다.

    폴백하면 운영에서 `[stub]` 문구가 학생에게 그대로 나가고, 에러가 없어서
    아무도 모른다.
    """
    from app.engines.analysis.engine import RealAnalysisEngine

    monkeypatch.setattr(get_settings(), "engine_mode", "real")
    try:
        assert isinstance(get_analysis_engine(), RealAnalysisEngine)
    finally:
        monkeypatch.setattr(get_settings(), "engine_mode", "stub")


def test_router_uses_injected_engine():
    """의존성 오버라이드로 가짜 엔진을 꽂으면 응답에 그 값이 나온다.

    통과 = 라우터가 엔진을 '주입받아' 쓴다는 증거. 9단계 이식의 안전장치.
    """

    class FakeEngine:
        def analyze(self, request: dict[str, Any], zip_bytes: bytes | None = None) -> dict[str, Any]:
            return {
                "snapshot_id": "fake-snap",
                "snapshot_meta": {"content_hash": "f" * 64, "file_count": 1, "byte_count": 1},
                "applied_scope": "TOTAL",
                "scope_fallback": False,
                "fallback_reason": None,
                "commit_sha": None,
                "analysis_document": {"overview": "", "structure": [],
                                      "decision_points": [], "risks": []},   # 필수. 못 만들었으면 빈 문자열을 명시적으로
                "problems": [],
                "question_count_planned": 0,
            }

    # 라우터가 쓰는 get_analysis_engine을 FakeEngine 반환으로 갈아끼운다.
    app.dependency_overrides[get_analysis_engine] = lambda: FakeEngine()
    try:
        post = client.post("/api/v0/analyses", json=VALID_BODY, headers=HEADERS)
        job_id = post.json()["jobId"]
        body = client.get(f"/api/v0/analyses/{job_id}", headers=HEADERS).json()

        assert body["status"] == "SUCCEEDED"                  # 계약 위반이면 여기서 먼저 드러난다
        assert body["result"]["snapshotId"] == "fake-snap"    # 가짜 엔진 값이 응답까지 흘렀다
    finally:
        app.dependency_overrides.clear()  # 다른 테스트에 오염 안 되게 반드시 청소
        
def test_stub_echoes_focus_item_ids():
    """요청 focusItems[].id가 problem에 그대로 돌아오는지 — C-1 계약 배선 확인."""
    raw = StubAnalysisEngine().analyze(
        {
            "extraction_scope": "TOTAL",
            "question_budget": 3,
            "focus_items": [{"id": "focus-a", "name": "예외 처리"}],
        }
    )

    # 후보가 1개뿐이면 첫 문제만 물고 나머지는 자율 선정(None)
    assert [p["question_focus_item_id"] for p in raw["problems"]] == ["focus-a", None, None]
    
def test_stub_judges_every_requirement():
    """requirementResults는 요청 requirements와 1:1. 빠뜨리면 미판정이 통과로 기록된다."""
    raw = StubAnalysisEngine().analyze(
        {
            "extraction_scope": "TOTAL",
            "question_budget": 3,
            "requirements": [
                {"requirementId": "req-1", "text": "로그인 구현"},
                {"requirementId": "req-2", "text": "예외 처리"},
            ],
        }
    )

    assert [r["requirement_id"] for r in raw["requirement_results"]] == ["req-1", "req-2"]
    assert raw["analysis_document"]["overview"]
    
def test_invalid_evidence_cannot_carry_line_numbers():
    """근거를 못 찾았는데 줄 번호가 붙어 있으면 막는다 — 지어낸 위치가 근거로 새는 경로."""
    from pydantic import ValidationError

    from app.schemas.analysis import DecisionPoint

    with pytest.raises(ValidationError):
        DecisionPoint(
            title="t", source_path="a.py", symbol="def f():",
            line_start=1, line_end=2,
            why_it_matters="w", evidence_valid=False,
        )