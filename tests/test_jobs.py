""" 6단계 job 수명주기 테스트. HTTP 없이 jobs.py 함수를 직접 검증한다. """
from typing import Any

from app.engines.analysis import fetch as fetch_engine
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

# analysisInput 경로(M3, D2) -- method/source 대신 이걸로 재fetch한다.
ANALYSIS_INPUT_BODY = AnalysisRequest.model_validate({
    "extractionScope": "TOTAL",
    "questionBudget": 3,
    "analysisInput": {
        "analysisInputId": "11111111-1111-1111-1111-111111111111",
        "method": "GITHUB_URL",
        "repositoryUrl": "https://github.com/owner/repo",
        "resolvedBranch": "main",
        "headCommitSha": "a" * 40,
        "inputHash": "0" * 64,
    },
})


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
    # 🔴 잠정(계획 §0.3) -- 세분화할 신호가 없어 catch-all이다. 근거 없이 더 구체적인
    # 값(TIMEOUT 등)을 추측하는 것보다 이쪽이 정직하다.
    assert job.failure_code == "PROVIDER_ERROR"


def test_run_analysis_via_analysis_input_uses_prefetched_root(monkeypatch, tmp_path):
    """analysisInput이 있으면 refetch_pinned()로 재fetch하고, 엔진에는 그 결과 루트를

    prefetched_root로 넘겨야 한다(D2) -- 엔진이 따로 클론하면 검증했던 것과 다른
    코드(그 사이 브랜치가 움직였을 수 있다)를 볼 위험이 되살아난다.
    """
    fake_root = tmp_path / "refetched"
    fake_root.mkdir()

    def fake_refetch(descriptor):
        assert descriptor["head_commit_sha"] == "a" * 40
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            yield fetch_engine.FetchedInput(
                root=str(fake_root), method="GITHUB_URL", resolved_branch="main",
                head_commit={"sha": "a" * 40, "message": "m",
                             "committed_at": "2026-01-01T00:00:00Z"},
                input_hash="0" * 64, file_count=1, byte_count=1,
            )
        return _cm()

    monkeypatch.setattr(fetch_engine, "refetch_pinned", fake_refetch)

    received: dict[str, Any] = {}

    class _RecordingEngine:
        def analyze(self, request, zip_bytes=None, *, prefetched_root=None):
            received["method"] = request.get("method")
            received["zip_bytes"] = zip_bytes
            received["prefetched_root"] = prefetched_root
            return StubAnalysisEngine().analyze(request, zip_bytes)

    job = create_job(ANALYSIS_INPUT_BODY, idempotency_key=None)
    run_analysis(job.job_id, ANALYSIS_INPUT_BODY, _RecordingEngine(), zip_bytes=None)

    assert job.status == "SUCCEEDED"
    # 최상위 method는 analysisInput 경로에선 비어 있을 수 있다 -- backfill 확인.
    assert received["method"] == "GITHUB_URL"
    assert received["zip_bytes"] is None
    assert received["prefetched_root"] == str(fake_root)


def test_run_analysis_marks_fetch_error_with_its_own_failure_code(monkeypatch):
    """재fetch가 실패하면(호스트 거부·해시 불일치 등) FetchError의 failureCode를

    재분류 없이 그대로 옮긴다 -- fetch.py가 이미 정확한 코드를 골랐다.
    """
    def fake_refetch(descriptor):
        raise fetch_engine.FetchError("INPUT_HASH_MISMATCH", "재fetch한 코드가 검증했던 것과 다릅니다")

    monkeypatch.setattr(fetch_engine, "refetch_pinned", fake_refetch)

    job = create_job(ANALYSIS_INPUT_BODY, idempotency_key=None)
    run_analysis(job.job_id, ANALYSIS_INPUT_BODY, StubAnalysisEngine(), zip_bytes=None)

    assert job.status == "FAILED"
    assert job.failure_code == "INPUT_HASH_MISMATCH"
    assert "검증했던 것과 다릅니다" in job.failure_reason
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
