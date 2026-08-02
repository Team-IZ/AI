""" 보고서 엔드포인트 스텁 테스트. 분석과 같은 비동기 job 패턴.

**보고서는 문제 단위다**(2026-08-02). 문제 하나가 끝날 때마다 한 번 부르고,
세션 1회에 보고서가 3개 나온다. 스텁은 problemId로 시나리오를 고른다.
"""
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

BODY = {"problemId": "prob-stub-1", "sessionId": "s-1", "scoreRunId": "run-1", "transcript": []}


def _create(problem_id: str = "prob-stub-1") -> str:
    r = client.post("/api/v0/reports", json={**BODY, "problemId": problem_id}, headers=HEADERS)
    assert r.status_code == 202
    return r.json()["jobId"]


def _result(problem_id: str = "prob-stub-1") -> dict:
    job_id = _create(problem_id)
    return client.get(f"/api/v0/reports/{job_id}", headers=HEADERS).json()["result"]


def test_accepts_report_request():
    """정상 요청 → 202 + QUEUED, camelCase jobId."""
    r = client.post("/api/v0/reports", json=BODY, headers=HEADERS)

    assert r.status_code == 202
    assert r.json()["status"] == "QUEUED"
    assert r.json()["jobId"]


def test_problem_id_is_required():
    """보고서는 문제 단위다 — 어느 문제인지 없이 만들 수 없다."""
    body = {k: v for k, v in BODY.items() if k != "problemId"}

    assert client.post("/api/v0/reports", json=body, headers=HEADERS).status_code == 422


def test_report_completes():
    """폴링하면 SUCCEEDED. status는 analysis_job과 같은 어휘를 쓴다."""
    body = client.get(f"/api/v0/reports/{_create()}", headers=HEADERS).json()

    assert body["status"] == "SUCCEEDED"
    assert body["problemId"] == "prob-stub-1"   # 어느 문제의 보고서인지 응답에 남는다


def test_result_carries_one_problem():
    """결과는 문제 하나 분량이다. 세션 전체를 담지 않는다."""
    result = _result()

    assert "problems" not in result           # 옛 세션 단위 필드
    assert result["problem"]["problemId"] == "prob-stub-1"
    assert [s["axisCode"] for s in result["problem"]["stages"]] == ["L1", "L2", "L3", "L4"]


def test_hint_cap_lowers_recorded_score():
    """힌트 2회를 쓰면 원점수 5여도 기록 점수는 상한 3으로 깎인다."""
    l4 = _result()["problem"]["stages"][3]

    assert l4["bestScore"] == 5
    assert l4["confirmedScore"] == 3
    assert l4["hintsUsed"] == 2
    assert l4["attemptCount"] == 3
    assert l4["autonomy"] == "PARTIAL"


def test_unreached_level_has_no_score():
    """도달 못 한 단계는 attemptCount=0이고 점수가 비어 있다."""
    stages = _result("prob-stub-3")["problem"]["stages"]   # L1에서 끝난다

    assert stages[0]["attemptCount"] > 0
    for s in stages[1:]:
        assert s["attemptCount"] == 0
        assert s["confirmedScore"] is None
        assert s["bestScore"] is None


def test_retest_needs_both_l1_and_l2():
    """재시험은 L1·L2 둘 다 통과해야 아니다 — L2 실패도 재시험이다."""
    assert _result("prob-stub-1")["retest"] is False   # 완주
    assert _result("prob-stub-2")["retest"] is True    # L1 통과, L2 미달
    assert _result("prob-stub-3")["retest"] is True    # L1 미달


def test_max_score_is_twenty_per_problem():
    """문제 만점 = 4단계 × 5점 = 20. 세션 총점은 AI가 보내지 않는다."""
    problem = _result()["problem"]

    assert problem["maxScore"] == 20
    assert 0 <= problem["totalScore"] <= 20
    assert "summary" not in _result()


def test_report_carries_curriculum_refs():
    """보고서에 교안 참조가 실려야 '어디를 복습하라'가 가능하다."""
    result = _result()

    assert result["reportMarkdown"]
    assert result["curriculumRefs"][0]["sourcePages"]


def test_unknown_report_job_returns_404():
    """모르는 jobId → 404 JOB_NOT_FOUND, retryable=false."""
    r = client.get("/api/v0/reports/nope", headers=HEADERS)

    assert r.status_code == 404
    assert r.json()["error"] == "JOB_NOT_FOUND"
    assert r.json()["retryable"] is False


def test_report_stages_must_be_four_in_order():
    """도달 못 한 단계를 빼고 보내면 막는다 — Spring이 어느 problem_stage 행인지 못 찾는다."""
    import pytest
    from pydantic import ValidationError

    from app.schemas.report import ProblemResult

    only_l1 = [{"axisCode": "L1", "attemptCount": 3, "passed": False, "hintsUsed": 2}]
    with pytest.raises(ValidationError):
        ProblemResult(problemNo=1, problemId="p-1", totalScore=2, maxScore=20, stages=only_l1)
