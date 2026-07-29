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
    questions = _result()["summary"]["questions"]

    assert len(questions) == 3
    for q in questions:
        assert [lv["axisCode"] for lv in q["levels"]] == [
            "L1_CODE_DESCRIPTION", "L2_DESIGN_LOGIC",
            "L3_COUNTEREXAMPLE", "L4_ALTERNATIVE",
        ]


def test_hint_cap_lowers_recorded_score():
    """힌트 2회를 쓰면 원점수 5여도 기록 점수는 상한 3으로 깎인다."""
    levels = _result()["summary"]["questions"][0]["levels"]
    l4 = levels[3]

    assert l4["rawScore"] == 5
    assert l4["score"] == 3
    assert l4["hintsUsed"] == 2
    assert l4["autonomy"] == "PARTIAL"


def test_unreached_level_has_no_score():
    """도달 못 한 레벨은 reached=false이고 점수가 비어 있다."""
    # dp-stub-3은 L1에서 끝난다 → L2~L4 미도달
    levels = _result()["summary"]["questions"][2]["levels"]

    assert levels[0]["reached"] is True
    for lv in levels[1:]:
        assert lv["reached"] is False
        assert lv["score"] is None
        assert lv["rawScore"] is None


def test_retest_targets_match_l1_failures():
    """재시험 대상은 L1에서 막힌 문제뿐이다(L2 실패는 대상 아님)."""
    result = _result()
    questions = result["summary"]["questions"]

    assert questions[0]["needsRetest"] is False   # 완주
    assert questions[1]["needsRetest"] is False   # L2에서 종료
    assert questions[2]["needsRetest"] is True    # L1에서 종료
    assert result["retestTargets"] == ["dp-stub-3"]


def test_failed_at_marks_where_question_ended():
    """failedAt은 힌트 소진 후 미달로 끝난 축을 가리킨다. 완주면 null."""
    questions = _result()["summary"]["questions"]

    assert questions[0]["failedAt"] is None
    assert questions[1]["failedAt"] == "L2_DESIGN_LOGIC"
    assert questions[2]["failedAt"] == "L1_CODE_DESCRIPTION"


def test_max_score_reflects_four_levels():
    """만점 = 문제 3개 × 4레벨 × 5점 = 60."""
    summary = _result()["summary"]

    assert summary["maxScore"] == 60
    assert 0 <= summary["totalScore"] <= summary["maxScore"]


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
