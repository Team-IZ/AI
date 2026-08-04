""" 6단계 job 수명주기 테스트. HTTP 없이 jobs.py 함수를 직접 검증한다. """
from typing import Any

from app.engines.stub import StubAnalysisEngine
from app.jobs import create_job, get_job, run_analysis
from app.schemas.analysis import AnalysisRequest

BODY = AnalysisRequest.model_validate(
    {
        "method": "GITHUB_URL",
        "source": {"repoUrl": "https://github.com/owner/repo"},
        "extractionScope": "TOTAL",
        "questionBudget": 4,
    }
)


def test_job_starts_queued():
    """생성 직후는 QUEUED이고 결과가 없다(아직 분석 안 함)."""
    job = create_job(BODY, idempotency_key=None)

    assert job.status == "QUEUED"
    assert job.result is None
    assert get_job(job.job_id) is job  # 저장소에 같은 객체가 들어가 있다


def test_run_analysis_reaches_succeeded():
    """run_analysis를 돌리면 SUCCEEDED로 전이하고 결과·타임스탬프가 채워진다."""
    job = create_job(BODY, idempotency_key=None)

    run_analysis(job.job_id, BODY, StubAnalysisEngine(), zip_bytes=None)

    assert job.status == "SUCCEEDED"
    assert job.result is not None
    assert job.started_at is not None
    assert job.completed_at is not None


def test_run_analysis_failed_on_engine_error():
    """엔진이 예외를 던지면 FAILED로 전이하고 사유를 기록한다(예외를 삼키지 않는다)."""

    class BoomEngine:
        def analyze(self, request: dict[str, Any], zip_bytes: bytes | None = None) -> dict[str, Any]:
            raise RuntimeError("boom")

    job = create_job(BODY, idempotency_key=None)

    run_analysis(job.job_id, BODY, BoomEngine(), zip_bytes=None)

    assert job.status == "FAILED"
    assert job.failure_reason == "boom"
    assert job.result is None

def test_requirement_failure_makes_the_job_partial():
    """요구사항 판정만 실패하면 PARTIAL이다.

    SUCCEEDED로 덮으면 화면에 "요구사항 전부 미충족"이 사실처럼 뜬다 — 문제·질문·힌트는
    정상으로 나가는데도. 실호출에서 실제로 나오는 경로다.
    """
    body = AnalysisRequest.model_validate({
        "method": "ZIP_WITH_GITLOG",
        "requirements": [{"requirementId": "r1", "text": "로그인"}],
    })
    job = create_job(body, idempotency_key=None)

    class _Engine:
        def analyze(self, request, zip_bytes=None):
            raw = StubAnalysisEngine().analyze(request, zip_bytes)
            raw["requirement_results"] = [
                {"requirement_id": "r1", "verdict": "FAIL", "evidence": None,
                 "note": "판정 실패: p04-2 터짐"}
            ]
            return raw

    run_analysis(job.job_id, body, _Engine(), zip_bytes=None)

    assert job.status == "PARTIAL"
    assert "요구사항 판정 1건" in job.failure_reason
    assert job.result.problems              # 문답은 살아 있다
