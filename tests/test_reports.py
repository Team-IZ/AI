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


def test_each_attempt_has_its_own_slot():
    """힌트를 쓴 축은 앞 슬롯이 미통과로 차 있어야 DB CHECK를 통과한다."""
    l4 = _result()["problem"]["stages"][3]

    assert l4["questionPassed"] is False
    assert l4["firstHintPassed"] is False
    assert (l4["secondHintScore"], l4["secondHintPassed"]) == (5, True)
    assert l4["status"] == "PASSED"


def test_unreached_level_has_no_score():
    """도달 못 한 단계는 NOT_REACHED이고 점수가 전부 null이다."""
    stages = _result("prob-stub-3")["problem"]["stages"]   # L1에서 끝난다

    assert stages[0]["status"] == "NOT_PASSED"
    for s in stages[1:]:
        assert s["status"] == "NOT_REACHED"
        assert s["questionScore"] is None
        assert s["firstHintScore"] is None
        assert s["secondHintScore"] is None


def test_retest_needs_both_l1_and_l2():
    """재시험은 L1·L2 둘 다 통과해야 아니다 — L2 실패도 재시험이다."""
    assert _result("prob-stub-1")["retest"] is False   # 완주
    assert _result("prob-stub-2")["retest"] is True    # L1 통과, L2 미달
    assert _result("prob-stub-3")["retest"] is True    # L1 미달


def test_no_total_score_anywhere():
    """총점을 만들지 않는다 — 보상 금지(§5-1). 대신 reachedStage가 판정값이다."""
    problem = _result()["problem"]

    assert "totalScore" not in problem
    assert "maxScore" not in problem
    assert "summary" not in _result()


def test_reached_stage_matches_passes():
    """도달 단계 = 앞에서부터 연속 통과한 개수. 계단이라 건너뛴 통과는 없다."""
    assert _result("prob-stub-1")["problem"]["reachedStage"] == 4   # 완주
    assert _result("prob-stub-2")["problem"]["reachedStage"] == 1   # L1만 통과
    assert _result("prob-stub-3")["problem"]["reachedStage"] == 0   # L1 미달


def test_reached_stage_cannot_contradict_stages():
    """파생값이라 따로 보내면 어긋날 수 있다. 어긋나면 판정과 근거가 다른 말을 한다."""
    import pytest
    from pydantic import ValidationError

    from app.schemas.report import ProblemResult

    all_failed = [
        {"axisCode": a, "attemptCount": 3, "passed": False, "hintsUsed": 2}
        for a in ("L1", "L2", "L3", "L4")
    ]
    with pytest.raises(ValidationError):
        ProblemResult(problemNo=1, problemId="p-1", reachedStage=2, stages=all_failed)


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


def test_same_idempotency_key_returns_same_job_id():
    """{problemId}:{scoreRunId} 재전송 → 같은 jobId. LLM을 두 번 부르지 않는다.

    보고서는 문제마다 1건이라 중복이 곧 비용이다(§T11 D-2).
    """
    headers = {**HEADERS, "Idempotency-Key": "prob-stub-1:run-1"}

    first = client.post("/api/v0/reports", json=BODY, headers=headers)
    second = client.post("/api/v0/reports", json=BODY, headers=headers)

    assert first.json()["jobId"] == second.json()["jobId"]
    assert client.post("/api/v0/reports", json=BODY,
                       headers={**HEADERS, "Idempotency-Key": "prob-stub-1:run-2"}
                       ).json()["jobId"] != first.json()["jobId"]


def test_ai_usage_is_reported_for_reports(monkeypatch):
    """🔴 보고서 토큰이 원장에 실려야 한다 (§T11 F1). 엔진이 주는데 버리고 있었다."""
    from datetime import datetime, timezone

    from app.engines.analysis import stages

    usage = {"model_code": "m-1", "input_token_count": 900, "output_token_count": 120,
             "cached_token_count": 0, "status": "SUCCEEDED", "failure_code": None,
             "latency_ms": 3000, "occurred_at": datetime.now(timezone.utc)}
    monkeypatch.setattr(get_settings(), "engine_mode", "real")
    monkeypatch.setattr(stages, "call",
                        lambda *a, **k: stages.StageResult(data={"summary": "요약"},
                                                           usages=[usage]))
    try:
        r = client.post("/api/v0/reports", json={"problemId": "prob-1", "providerModelCode": "vendor/m"},
                        headers={**HEADERS, "X-Trace-Id": "trace-9"})
        job = client.get(f"/api/v0/reports/{r.json()['jobId']}", headers=HEADERS).json()
    finally:
        monkeypatch.setattr(get_settings(), "engine_mode", "stub")

    assert len(job["aiUsage"]) == 1
    assert job["aiUsage"][0]["featureCode"] == "REPORT_GENERATION"
    assert job["aiUsage"][0]["contextType"] == "REPORT_SNAPSHOT"
    assert job["aiUsage"][0]["traceId"] == "trace-9"        # 헤더가 원장으로 이어진다
    assert job["aiUsage"][0]["outputTokenCount"] == 120


def test_camelcase_transcript_reaches_the_engine(monkeypatch):
    """와이어는 camelCase인데 엔진은 snake_case를 읽는다 — 안 바꾸면 조용히 다 버린다.

    2026-08-02 실호출에서 발견: 턴 3개를 넘겼는데 도달 0단·재시험 True가 나왔고
    모델이 "문답 기록이 없다"고 썼다. **에러는 안 났다.**

    transcript만 list[dict] 원시 타입이라 pydantic의 alias 변환을 안 탄다.
    """
    from app.config import get_settings
    from app.engines.analysis import stages

    monkeypatch.setattr(get_settings(), "engine_mode", "real")
    monkeypatch.setattr(
        stages, "call",
        lambda *a, **k: stages.StageResult(data={"summary": "요약"}, usages=[]),
    )

    wire_turn = {
        "problemId": "prob-1", "axisCode": "L1", "questionText": "q",
        "answerText": "a", "answeredAt": "2026-08-02T00:00:00Z",
        "score": 4, "passed": True, "hintsUsed": 0,
    }
    try:
        r = client.post("/api/v0/reports", headers=HEADERS, json={
            "problemId": "prob-1", "transcript": [wire_turn], "providerModelCode": "vendor/m",
        })
        result = client.get(f"/api/v0/reports/{r.json()['jobId']}",
                            headers=HEADERS).json()["result"]
    finally:
        monkeypatch.setattr(get_settings(), "engine_mode", "stub")

    assert result["problem"]["reachedStage"] == 1        # 0이면 턴을 못 읽은 것
    assert result["problem"]["stages"][0]["questionScore"] == 4
    assert result["retest"] is True                      # L2 미도달이라 재시험은 맞다
