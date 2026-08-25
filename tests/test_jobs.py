""" 6단계 job 수명주기 테스트. HTTP 없이 jobs.py 함수를 직접 검증한다. """
import threading
import time
from typing import Any

from app import jobs as jobs_module
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
        # 2026-08-10: problemScope 기본값 TEAM_SHARED_PROBLEM은 teaches를 요구한다.
        # 빈 teaches로 팀 문제를 내면 조용히 0개가 나가고, DB도 개념 NOT NULL이라
        # 저장 자체가 안 된다.
        "teaches": [{"id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed", "label": "제네릭 타입 경계"}],
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


def test_analysis_concurrency_is_capped(monkeypatch):
    """동시 job이 `_ANALYSIS_CONCURRENCY` 상한을 넘지 않는다.

    2026-08-25 실제 인시던트(jobs.py의 D4 주석 참고) 회귀 테스트 -- job 12개가
    거의 동시에 시작되며 단일 인스턴스 CPU를 100%로 포화시켜 헬스체크조차
    응답을 못 만드는 상태(curl 15초 타임아웃 실측)까지 갔다. 상한을 넘는 job은
    QUEUED로 대기해야 한다.
    """
    monkeypatch.setattr(jobs_module, "HEAVY_JOB_CONCURRENCY", threading.Semaphore(2))

    lock = threading.Lock()
    current = 0
    max_seen = 0

    class _SlowEngine:
        def analyze(self, request: dict[str, Any], zip_bytes: bytes | None = None) -> dict[str, Any]:
            nonlocal current, max_seen
            with lock:
                current += 1
                max_seen = max(max_seen, current)
            time.sleep(0.05)
            raw = StubAnalysisEngine().analyze(request, zip_bytes)
            with lock:
                current -= 1
            return raw

    jobs = [create_job(BODY, idempotency_key=None) for _ in range(5)]
    threads = [
        threading.Thread(target=run_analysis, args=(j.job_id, BODY, _SlowEngine(), None))
        for j in jobs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert max_seen == 2   # 상한(2)을 실제로 채웠고 넘지는 않았다
    assert all(j.status == "SUCCEEDED" for j in jobs)


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

def test_scan_limit_is_file_too_large_not_model_error():
    """🔴 2026-08-10 회귀 — 스캔 규모 상한이 MODEL_ERROR로 보고됐다.

    이 실패는 fetch가 아니라 **엔진 스캔 단계**에서 나서 FetchError 분기에 안 걸리고
    catch-all까지 새어나갔다. LLM을 한 번도 안 불렀는데(ai_usage 비어 있음) 백엔드는
    "모델 실패"로 읽는다 -- git 바이너리 부재와 같은 계열의 오분류다.
    """
    from app.engines.analysis import rules

    class _Engine:
        def analyze(self, request: dict[str, Any], zip_bytes: bytes | None) -> dict[str, Any]:
            raise rules.ScanLimitExceeded("제출물 총 용량이 상한(100 bytes)을 넘습니다")

    job = create_job(BODY, idempotency_key=None)
    run_analysis(job.job_id, BODY, _Engine(), None)

    assert job.status == "FAILED"
    assert job.failure_code == "FILE_TOO_LARGE"
    assert job.failure_code in fetch_engine.VERIFICATION_FAILURE_CODES


def test_requirement_failure_makes_the_job_partial():
    """요구사항 판정만 실패하면 PARTIAL이다.

    SUCCEEDED로 덮으면 화면에 "요구사항 전부 미충족"이 사실처럼 뜬다 — 문제·질문·힌트는
    정상으로 나가는데도. 실호출에서 실제로 나오는 경로다.
    """
    body = AnalysisRequest.model_validate({
        "method": "ZIP_WITH_GITLOG",
        "requirements": [{"requirementId": "r1", "text": "로그인"}],
        "teaches": [{"id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed", "label": "제네릭 타입 경계"}],
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
    from app.job_store import InMemoryJobStore

    monkeypatch.setattr(jobs_module, "_store", InMemoryJobStore(max_items=3))

    first = create_job(BODY, idempotency_key=None)
    for _ in range(3):
        create_job(BODY, idempotency_key=None)

    assert get_job(first.job_id) is None
    assert len(jobs_module._store._jobs) == 3


def test_evicted_jobs_idempotency_key_is_treated_as_fresh(monkeypatch):
    """멱등키가 가리키던 job이 상한으로 밀려났으면 신원불일치(409)가 아니라
    '처음 보는 키'로 취급해야 한다."""
    from app import jobs as jobs_module
    from app.job_store import InMemoryJobStore

    monkeypatch.setattr(jobs_module, "_store", InMemoryJobStore(max_items=3))

    create_job(BODY, idempotency_key="evict-me")
    for _ in range(3):
        create_job(BODY, idempotency_key=None)

    assert jobs_module.job_id_for_key("evict-me", BODY.submission_id, BODY.attempt_id) is None
