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

# 백엔드가 처음 /analysis-inputs 때 에코해준 gitHistory가 실려 있는 버전 -- D2 재fetch가
# 히스토리를 다시 못 얻었을 때 이걸로 폴백하는지 검증하는 데 쓴다.
ANALYSIS_INPUT_BODY_WITH_ECHOED_HISTORY = AnalysisRequest.model_validate({
    "extractionScope": "TOTAL",
    "questionBudget": 3,
    "analysisInput": {
        "analysisInputId": "11111111-1111-1111-1111-111111111111",
        "method": "GITHUB_URL",
        "repositoryUrl": "https://github.com/owner/repo",
        "resolvedBranch": "main",
        "headCommitSha": "a" * 40,
        "inputHash": "0" * 64,
        "gitHistory": [{
            "sha": "a" * 40, "authorName": "Alice", "authorEmail": "a@x.com",
            "committedAt": "2026-01-01T00:00:00Z", "changedFiles": [],
            "additions": 0, "deletions": 0, "parentSha": "0" * 40,
            "authoredAt": "2026-01-01T00:00:00Z", "branchName": "main",
            "isMergeCommit": False, "isRevertCommit": False,
            "isBotCommit": False, "changedLineCount": 0,
        }],
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
    # 값(ANALYSIS_TIMEOUT 등)을 추측하는 것보다 이쪽이 정직하다. MODEL_ERROR는
    # analysis_job.failure_code의 DB CHECK 15종 안에 있는 값이다(옛 PROVIDER_ERROR는
    # ai_usage 네임스페이스 값이라 여기선 무효였다 -- 2026-08-07 버그 수정).
    assert job.failure_code == "MODEL_ERROR"


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
        def analyze(self, request, zip_bytes=None, *, prefetched_root=None, prefetched_git=None):
            received["method"] = request.get("method")
            received["zip_bytes"] = zip_bytes
            received["prefetched_root"] = prefetched_root
            received["prefetched_git"] = prefetched_git
            return StubAnalysisEngine().analyze(request, zip_bytes)

    job = create_job(ANALYSIS_INPUT_BODY, idempotency_key=None)
    run_analysis(job.job_id, ANALYSIS_INPUT_BODY, _RecordingEngine(), zip_bytes=None)

    assert job.status == "SUCCEEDED"
    # 최상위 method는 analysisInput 경로에선 비어 있을 수 있다 -- backfill 확인.
    assert received["method"] == "GITHUB_URL"
    assert received["zip_bytes"] is None
    assert received["prefetched_root"] == str(fake_root)
    # D-analysis-b1 -- refetch_pinned()가 이미 갖고 있던 resolved_branch/head_commit이
    # 엔진에도 그대로 전달돼야 한다(재계산 없이).
    assert received["prefetched_git"]["resolved_branch"] == "main"
    assert received["prefetched_git"]["head_commit"]["sha"] == "a" * 40


def test_run_analysis_falls_back_to_backend_echoed_git_history_when_refetch_is_empty(
    monkeypatch, tmp_path,
):
    """D-analysis-b1 -- 재fetch한 git_history가 비면(네트워크 flake 등) 백엔드가 처음

    /analysis-inputs 때 에코해준 request.analysis_input.gitHistory로 폴백한다. 같은
    pinned sha의 이미 검증된 데이터라 정보 손실만 막고 틀릴 수 없다.
    """
    fake_root = tmp_path / "refetched"
    fake_root.mkdir()

    def fake_refetch(descriptor):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            yield fetch_engine.FetchedInput(
                root=str(fake_root), method="GITHUB_URL", resolved_branch="main",
                head_commit={"sha": "a" * 40, "message": "m",
                             "committed_at": "2026-01-01T00:00:00Z"},
                git_history=[],  # 재fetch 히스토리 수집 실패 상황을 흉내낸다
                git_history_source="NONE",
                input_hash="0" * 64, file_count=1, byte_count=1,
            )
        return _cm()

    monkeypatch.setattr(fetch_engine, "refetch_pinned", fake_refetch)

    received: dict[str, Any] = {}

    class _RecordingEngine:
        def analyze(self, request, zip_bytes=None, *, prefetched_root=None, prefetched_git=None):
            received["prefetched_git"] = prefetched_git
            return StubAnalysisEngine().analyze(request, zip_bytes)

    job = create_job(ANALYSIS_INPUT_BODY_WITH_ECHOED_HISTORY, idempotency_key=None)
    run_analysis(job.job_id, ANALYSIS_INPUT_BODY_WITH_ECHOED_HISTORY, _RecordingEngine(),
                 zip_bytes=None)

    assert job.status == "SUCCEEDED"
    assert received["prefetched_git"]["git_history_source"] == "BACKEND_SUPPLIED"
    assert len(received["prefetched_git"]["git_history"]) == 1
    assert received["prefetched_git"]["git_history"][0]["sha"] == "a" * 40


def test_run_analysis_marks_fetch_error_with_its_own_failure_code(monkeypatch):
    """재fetch가 실패하면(호스트 거부·해시 불일치 등) FetchError의 failureCode를

    analysis_job.failure_code의 DB 15종으로 번역해서 옮긴다 -- fetch.py 내부 어휘를
    그대로 옮기면 DB CHECK 밖의 값(예: INPUT_HASH_MISMATCH)이 새어나간다.
    """
    def fake_refetch(descriptor):
        raise fetch_engine.FetchError("INPUT_HASH_MISMATCH", "재fetch한 코드가 검증했던 것과 다릅니다")

    monkeypatch.setattr(fetch_engine, "refetch_pinned", fake_refetch)

    job = create_job(ANALYSIS_INPUT_BODY, idempotency_key=None)
    run_analysis(job.job_id, ANALYSIS_INPUT_BODY, StubAnalysisEngine(), zip_bytes=None)

    assert job.status == "FAILED"
    assert job.failure_code == "SOURCE_UNREACHABLE"
    assert "검증했던 것과 다릅니다" in job.failure_reason
    assert job.result is None


def test_fetch_failure_code_translation_covers_full_fetch_vocabulary():
    """fetch.py 내부 어휘가 나중에 늘어도 매핑 누락을 조용히 지나치지 않게 핀 고정."""
    from app.jobs import _FETCH_FAILURE_CODE_TRANSLATION

    assert (
        set(_FETCH_FAILURE_CODE_TRANSLATION)
        == fetch_engine.VERIFICATION_FAILURE_CODES | fetch_engine.JOB_ONLY_FAILURE_CODES
    )


def test_fetch_failure_code_translation_values_are_all_db_legal():
    """매핑 결과 자체가 analysis_job.failure_code의 DB 15종 밖으로 새지 않는지."""
    from typing import get_args

    from app.jobs import _FETCH_FAILURE_CODE_TRANSLATION
    from app.schemas.analysis import AnalysisJobFailureCode

    assert set(_FETCH_FAILURE_CODE_TRANSLATION.values()) <= set(get_args(AnalysisJobFailureCode))

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
