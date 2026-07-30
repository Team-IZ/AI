""" 보고서 엔드포인트 스텁 테스트. 분석과 같은 비동기 job 패턴. """
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

BODY = {"sessionId": "s-1", "scoreRunId": "run-1", "transcript": []}


def _create() -> str:
    r = client.post("/api/v0/reports", json=BODY, headers=HEADERS)
    assert r.status_code == 202
    return r.json()["jobId"]


def _result() -> dict:
    return client.get(f"/api/v0/reports/{_create()}", headers=HEADERS).json()["result"]


def test_accepts_report_request():
    """정상 요청 → 202 + QUEUED, camelCase jobId."""
    r = client.post("/api/v0/reports", json=BODY, headers=HEADERS)

    assert r.status_code == 202
    assert r.json()["status"] == "QUEUED"
    assert r.json()["jobId"]


def test_report_completes():
    """폴링하면 SUCCEEDED. status는 analysis_job과 같은 어휘를 쓴다."""
    body = client.get(f"/api/v0/reports/{_create()}", headers=HEADERS).json()

    assert body["status"] == "SUCCEEDED"
    assert body["status"] in {"QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"}


def test_summary_has_four_axes_per_question():
    """문제마다 L1~L4 네 축이 순서대로 다 있어야 한다(미도달 포함)."""
    problems = _result()["problems"]

    assert len(problems) == 3
    for p in problems:
        assert [s["axisCode"] for s in p["stages"]] == ["L1", "L2", "L3", "L4"]


def test_hint_cap_lowers_recorded_score():
    """힌트 2회를 쓰면 원점수 5여도 기록 점수는 상한 3으로 깎인다."""
    l4 = _result()["problems"][0]["stages"][3]

    assert l4["bestScore"] == 5
    assert l4["confirmedScore"] == 3
    assert l4["hintsUsed"] == 2
    assert l4["attemptCount"] == 3
    assert l4["autonomy"] == "PARTIAL"


def test_unreached_level_has_no_score():
    """도달 못 한 단계는 attemptCount=0이고 점수가 비어 있다."""
    # prob-stub-3은 L1에서 끝난다 → L2~L4 미도달
    stages = _result()["problems"][2]["stages"]

    assert stages[0]["attemptCount"] > 0
    for s in stages[1:]:
        assert s["attemptCount"] == 0
        assert s["confirmedScore"] is None
        assert s["bestScore"] is None


def test_retest_targets_match_l1_failures():
    """재시험 대상은 L1에서 막힌 문제뿐이다(L2 실패는 대상 아님)."""
    result = _result()

    assert result["problems"][0]["stages"][0]["passed"] is True   # 완주
    assert result["problems"][1]["stages"][0]["passed"] is True   # L2에서 종료
    assert result["problems"][2]["stages"][0]["passed"] is False  # L1에서 종료
    assert result["retestTargets"] == ["prob-stub-3"]


def test_max_score_is_twenty_per_problem():
    """문제 만점 = 4단계 × 5점 = 20. 세션 총점은 AI가 보내지 않는다."""
    result = _result()

    for p in result["problems"]:
        assert p["maxScore"] == 20
        assert 0 <= p["totalScore"] <= 20
    assert "summary" not in result


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
