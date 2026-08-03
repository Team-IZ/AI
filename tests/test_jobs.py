""" 6단계 job 수명주기 테스트. HTTP 없이 jobs.py 함수를 직접 검증한다. """
import json
from typing import Any

import app.jobs as jobs_mod
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


def test_measurement_logged_to_stdout_on_success(capsys):
    """ D-pr3: 터미널 전이(SUCCEEDED)마다 stdout에 측정 한 줄이 남는다 -- 컨테이너에서도
    항상 동작하는 경로가 이것 하나뿐이므로 stub 경로에서도 반드시 확인한다. """
    job = create_job(BODY, idempotency_key=None)
    run_analysis(job.job_id, BODY, StubAnalysisEngine(), zip_bytes=None)

    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("[pr3-measurement] "))
    record = json.loads(line[len("[pr3-measurement] "):])
    assert record["job_id"] == job.job_id
    assert record["status"] == "SUCCEEDED"
    assert record["started_at"] is not None
    assert record["completed_at"] is not None
    assert record["failure_reason"] is None


def test_measurement_logged_to_stdout_on_failure(capsys):
    """ FAILED로 끝나도(엔진 예외) 측정은 남고 failure_reason이 채워진다. """

    class BoomEngine:
        def analyze(self, request: dict[str, Any], zip_bytes: bytes | None = None) -> dict[str, Any]:
            raise RuntimeError("boom")

    job = create_job(BODY, idempotency_key=None)
    run_analysis(job.job_id, BODY, BoomEngine(), zip_bytes=None)

    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("[pr3-measurement] "))
    record = json.loads(line[len("[pr3-measurement] "):])
    assert record["status"] == "FAILED"
    assert record["failure_reason"] == "boom"


def test_measurement_writes_local_jsonl_file(tmp_path):
    """ docs/가 실제로 존재하는(로컬 개발) 환경에서는 파일에도 한 줄 쌓인다.
    _MEASUREMENTS_DIR는 conftest.py의 autouse fixture가 이미 tmp_path로 격리해둔다. """
    job = create_job(BODY, idempotency_key=None)
    run_analysis(job.job_id, BODY, StubAnalysisEngine(), zip_bytes=None)

    files = list((tmp_path / "measurements").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["job_id"] == job.job_id


def test_measurement_failure_never_breaks_job_result(monkeypatch):
    """ D6과 같은 원칙, 벨트-앤-브레이스: _log_measurement 자신의 내부 안전장치가
    (가정상) 뚫려도 run_analysis의 finally에 있는 두번째 방어가 job 결과를 지킨다.
    이 테스트는 _log_measurement 내부 안전장치를 일부러 우회해서 그 두번째 방어를
    검증한다(내부 안전장치 자체는 test_log_measurement_swallows_write_errors가 검증). """

    def _boom_log(_job):
        raise OSError("disk full, pretend")

    monkeypatch.setattr(jobs_mod, "_log_measurement", _boom_log)

    job = create_job(BODY, idempotency_key=None)
    run_analysis(job.job_id, BODY, StubAnalysisEngine(), zip_bytes=None)  # 예외 안 나야 함

    assert job.status == "SUCCEEDED"
    assert job.result is not None


def test_measurement_calls_carry_per_stage_source_type_and_failure_code(capsys):
    """ D-pr3b: _log_measurement의 record는 latency_ms 평면 리스트가 아니라
    호출별 source_type/status/failure_code/latency_ms를 담은 calls 리스트를 남긴다 --
    스테이지별(예: CODE_MAP만 FAILED) 귀속이 가능해야 한다는 D10 지적을 고친다. """
    from datetime import datetime, timezone

    from app.schemas.analysis import AnalysisJobStatus
    from app.schemas.usage import AiUsage

    job = AnalysisJobStatus(job_id="x", status="PARTIAL")
    job.ai_usage = [
        AiUsage.model_validate(
            {
                "featureCode": "CODE_ANALYSIS",
                "modelCode": "mistralai/mistral-medium-3.5-128b",
                "sourceType": "CODE_MAP",
                "sourceId": "sub-1",
                "requestId": "req-1",
                "traceId": "trace-1",
                "idempotencyKey": "sub-1:CODE_MAP:1",
                "inputTokenCount": 100,
                "outputTokenCount": 50,
                "status": "SUCCEEDED",
                "latencyMs": 2051,
                "occurredAt": datetime.now(timezone.utc).isoformat(),
            }
        ),
        AiUsage.model_validate(
            {
                "featureCode": "CODE_ANALYSIS",
                "modelCode": "mistralai/mistral-medium-3.5-128b",
                "sourceType": "DIAGRAM",
                "sourceId": "sub-1",
                "requestId": "req-2",
                "traceId": "trace-1",
                "idempotencyKey": "sub-1:DIAGRAM:1",
                "inputTokenCount": 100,
                "outputTokenCount": 0,
                "status": "FAILED",
                "failureCode": "TIMEOUT",
                "latencyMs": 30000,
                "occurredAt": datetime.now(timezone.utc).isoformat(),
            }
        ),
    ]

    jobs_mod._log_measurement(job)

    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("[pr3-measurement] "))
    record = json.loads(line[len("[pr3-measurement] "):])

    assert "latency_ms" not in record  # 평면 리스트는 더는 없다
    assert len(record["calls"]) == 2
    assert record["calls"][0] == {
        "source_type": "CODE_MAP", "status": "SUCCEEDED",
        "failure_code": None, "latency_ms": 2051,
    }
    assert record["calls"][1] == {
        "source_type": "DIAGRAM", "status": "FAILED",
        "failure_code": "TIMEOUT", "latency_ms": 30000,
    }


def test_log_measurement_swallows_write_errors(monkeypatch, capsys):
    """ 실제 안전장치 자체를 검증 -- 로컬 파일 쓰기가 뭘 던지든 _log_measurement는
    예외를 밖으로 절대 안 던진다(stdout 경로는 별개로 계속 동작). """
    from app.schemas.analysis import AnalysisJobStatus

    class ExplodingPath:
        def mkdir(self, *a, **kw):
            raise OSError("permission denied, pretend")

    monkeypatch.setattr(jobs_mod, "_MEASUREMENTS_DIR", ExplodingPath())

    job = AnalysisJobStatus(job_id="x", status="SUCCEEDED")
    jobs_mod._log_measurement(job)  # 예외 안 나면 통과

    out = capsys.readouterr().out
    assert "[pr3-measurement]" in out