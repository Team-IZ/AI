""" 8단계 채점 엔드포인트 스텁 테스트. 분석과 같은 job 패턴. """
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

BODY = {"sessionId": "s-1", "scoreRunId": "run-1", "transcript": []}


def _create() -> str:
    r = client.post("/api/v0/gradings", json=BODY, headers=HEADERS)
    assert r.status_code == 202
    return r.json()["jobId"]


def test_accepts_grading_request():
    """정상 요청 → 202 + QUEUED, camelCase jobId."""
    r = client.post("/api/v0/gradings", json=BODY, headers=HEADERS)

    assert r.status_code == 202
    assert r.json()["status"] == "QUEUED"
    assert r.json()["jobId"]


def test_grading_completes_with_five_axes():
    """폴링하면 COMPLETED + 정확히 5축."""
    job_id = _create()

    body = client.get(f"/api/v0/gradings/{job_id}", headers=HEADERS).json()

    assert body["status"] == "COMPLETED"
    assert len(body["result"]["axisScores"]) == 5


def test_grading_total_and_average():
    """총점·평균이 축 점수와 맞는지(스텁: 4점*5축 = 20, 평균 4.0)."""
    job_id = _create()

    result = client.get(f"/api/v0/gradings/{job_id}", headers=HEADERS).json()["result"]

    assert result["totalScore"] == 20
    assert result["averageScore"] == 4.0


def test_grading_status_uses_allowed_values():
    """status는 QUEUED/GRADING/COMPLETED/PARTIAL/FAILED 안에 있어야."""
    job_id = _create()

    body = client.get(f"/api/v0/gradings/{job_id}", headers=HEADERS).json()

    assert body["status"] in {"QUEUED", "GRADING", "COMPLETED", "PARTIAL", "FAILED"}


def test_unknown_grading_job_returns_404():
    """모르는 jobId → 404 JOB_NOT_FOUND, retryable=false."""
    r = client.get("/api/v0/gradings/nope", headers=HEADERS)

    assert r.status_code == 404
    assert r.json()["error"] == "JOB_NOT_FOUND"
    assert r.json()["retryable"] is False