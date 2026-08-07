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
    # 값(ANALYSIS_TIMEOUT 등)을 추측하는 것보다 이쪽이 정직하다. MODEL_ERROR는
    # analysis_job.failure_code의 DB CHECK 11종 안에 있는 값이다(옛 PROVIDER_ERROR는
    # ai_usage 네임스페이스 값이라 여기선 무효였다 -- 2026-08-07 버그 수정).
    assert job.failure_code == "MODEL_ERROR"


def test_verification_failure_codes_pass_through_to_db_unchanged():
    """검증 실패 11종은 이름 그대로 DB 값이어야 한다(백엔드 회신 2026-08-07).

    하나라도 DB CHECK 밖으로 나가면 _translate_failure_code()가 조용히
    SOURCE_UNREACHABLE로 떨어뜨려 사유가 뭉개진다 -- 그게 이 회신 이전 상태였다.
    """
    from typing import get_args

    from app.jobs import _translate_failure_code
    from app.schemas.analysis import AnalysisJobFailureCode

    assert fetch_engine.VERIFICATION_FAILURE_CODES <= set(get_args(AnalysisJobFailureCode))
    for code in fetch_engine.VERIFICATION_FAILURE_CODES:
        assert _translate_failure_code(code) == code


def test_fetch_failure_code_translation_values_are_all_db_legal():
    """fetch.py 어휘 전체가 DB 15종 밖으로 새지 않는지. 나중에 새 코드가 늘었는데
    DB CHECK에 없으면 SOURCE_UNREACHABLE로 떨어진다."""
    from typing import get_args

    from app.jobs import _translate_failure_code
    from app.schemas.analysis import AnalysisJobFailureCode

    legal = set(get_args(AnalysisJobFailureCode))
    vocabulary = fetch_engine.VERIFICATION_FAILURE_CODES
    assert {_translate_failure_code(c) for c in vocabulary} <= legal
    assert _translate_failure_code("A_CODE_THAT_DOES_NOT_EXIST_YET") in legal

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


# ── M9 (redteam audit, 2026-08-05): _jobs 상한/eviction ──────────────────────────
# 무제한 dict였다 -- 업로드 상한(H13)과 별개로 job 자체가 영원히 안 지워져 장기가동
# 시 메모리가 계속 는다. sessions.py의 _answered와 같은 OrderedDict+상한 패턴이
# 실제로 밀어내는지, 그리고 밀려난 job의 멱등키가 "불일치"가 아니라 "새 키"로
# 취급되는지(안 그러면 정상 재시도가 409로 막힌다) 검증한다. 실제 2000개를 다
# 만들지 않도록 상한 자체를 낮춰서 검증한다.

def test_old_jobs_are_evicted_past_the_cap(monkeypatch):
    """상한을 넘기면 가장 먼저 만든 job부터 밀려난다."""
    from app import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "_jobs", type(jobs_module._jobs)())
    monkeypatch.setattr(jobs_module, "_job_id_by_idempotency_key", type(jobs_module._job_id_by_idempotency_key)())
    monkeypatch.setattr(jobs_module, "_JOBS_MAX", 3)

    first = create_job(BODY, idempotency_key=None)
    for _ in range(3):
        create_job(BODY, idempotency_key=None)

    assert get_job(first.job_id) is None
    assert len(jobs_module._jobs) == 3


def test_evicted_jobs_idempotency_key_is_treated_as_fresh(monkeypatch):
    """멱등키가 가리키던 job이 상한으로 밀려났으면 신원불일치(409)가 아니라
    '처음 보는 키'로 취급해야 한다."""
    from app import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "_jobs", type(jobs_module._jobs)())
    monkeypatch.setattr(jobs_module, "_job_id_by_idempotency_key", type(jobs_module._job_id_by_idempotency_key)())
    monkeypatch.setattr(jobs_module, "_JOBS_MAX", 3)

    create_job(BODY, idempotency_key="evict-me")
    for _ in range(3):
        create_job(BODY, idempotency_key=None)

    assert jobs_module.job_id_for_key("evict-me", BODY.submission_id, BODY.attempt_id) is None
