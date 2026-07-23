""" 5단계 엔진 소켓 테스트.

핵심은 test_router_uses_injected_engine: 의존성 오버라이드로 엔진을 갈아끼워도
라우터가 그걸 그대로 쓰는지 확인. 통과 = 9단계에서 라우터 안 건드리고 엔진 교체 가능.
"""

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


def test_findings_use_decision_point_columns():
    """finding이 DB 컬럼명(dp_id, source_path 등)을 쓰는지. 임의 이름이면 실패."""
    raw = StubAnalysisEngine().analyze({"extraction_scope": "TOTAL", "question_budget": 1})
    finding = raw["findings"][0]

    assert "dp_id" in finding
    assert "source_path" in finding
    assert finding["references"][0]["reference_type"] == "PRIMARY"


def test_real_mode_raises_not_implemented(monkeypatch):
    """engine_mode=real인데 구현이 없으면 NotImplementedError. 조용한 폴백 금지."""
    monkeypatch.setattr(get_settings(), "engine_mode", "real")
    try:
        with pytest.raises(NotImplementedError):
            get_analysis_engine()
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
                "findings": [],
                "question_count_planned": 0,
            }

    # 라우터가 쓰는 get_analysis_engine을 FakeEngine 반환으로 갈아끼운다.
    app.dependency_overrides[get_analysis_engine] = lambda: FakeEngine()
    try:
        post = client.post("/api/v0/analyses", json=VALID_BODY, headers=HEADERS)
        job_id = post.json()["jobId"]
        result = client.get(f"/api/v0/analyses/{job_id}", headers=HEADERS).json()["result"]

        assert result["snapshotId"] == "fake-snap"  # 가짜 엔진 값이 응답까지 흘렀다
    finally:
        app.dependency_overrides.clear()  # 다른 테스트에 오염 안 되게 반드시 청소