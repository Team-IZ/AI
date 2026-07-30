""" 분석 job의 인메모리 저장소와 수명주기. 

프로세스 재시작 시 유실, 워커 1개에서만 동작
영속/스케일 필요시 Redis 나 DB로 이전, 지금은 단일 프로세스 개발용
"""

import uuid

from datetime import datetime, timezone

from app.engines.base import AnalysisEngine
from app.schemas.analysis import AnalysisJobStatus, AnalysisRequest, AnalysisResult
from app.schemas.usage import AiUsage

# job_id
_jobs: dict[str, AnalysisJobStatus] = {}

# 멱등성 키 -> job_id. 같은 키 재요청 시 새 job 안 만들고 처음 id 반환
_job_id_by_idempotency_key: dict[str, str] = {}

def get_job(job_id: str) -> AnalysisJobStatus | None:
    return _jobs.get(job_id)

def job_id_for_key(idempotency_key: str) -> str | None:
    return _job_id_by_idempotency_key.get(idempotency_key)

def create_job(body: AnalysisRequest, idempotency_key: str | None) -> AnalysisJobStatus:
    """ QUEUED 상태 job을 만들어 저장. 아직 분석 X -> result 없음 """
    job = AnalysisJobStatus(
        job_id=str(uuid.uuid4()),
        attempt_id=body.attempt_id,
        submission_id=body.submission_id,
        status="QUEUED",
    )
    
    _jobs[job.job_id] = job
    if idempotency_key:
        _job_id_by_idempotency_key[idempotency_key] = job.job_id
    return job

def run_analysis(
    job_id: str, body: AnalysisRequest, engine: AnalysisEngine, zip_bytes: bytes | None
) -> None:
    """ 백그라운드 워커. 상태 전이시키며 분석 수행 
    
    QUEUED -> RUNNING -> SUCCEEDED (엔진 터지면 FAILED)
    202 응답 나간 뒤 실행, 호출자는 폴링(GET)으로 이 전이 관측.
    
    _jobs에 담긴 객체를 그 자리에서 수정 -> GET이 같은 객체 돌려주므로 변경 그대로 보임
    """
    job = _jobs[job_id]
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)
    
    try:
        raw = engine.analyze(body.model_dump(), zip_bytes)
        # D-timing (2026-07-30): ai_usage는 AnalysisResult 필드가 아니라 엔진이
        # 얹어 보내는 형제 키다(app/engines/base.py 참고) -- 먼저 꺼내야
        # AnalysisResult.model_validate가 나머지만 보고 계약 위반을 정확히 잡는다.
        usage_raw = raw.pop("ai_usage", [])
        result = AnalysisResult.model_validate(raw)  # 계약 위반은 여기서 예외

        # 요구사항 판정은 빠짐없이 와야 한다. 모델이 몇 개를 조용히 빠뜨리면
        # 판정 안 된 요구사항이 통과로 기록된다 — 여기서 막는다.
        if len(result.requirement_results) != len(body.requirements):
            raise ValueError(
                f"requirementResults 개수가 요청 requirements와 다릅니다: "
                f"{len(result.requirement_results)} != {len(body.requirements)}"
            )

        job.result = result
        job.ai_usage = [AiUsage.model_validate(u) for u in usage_raw]
        job.status = "SUCCEEDED"
    except Exception as exc:
        # 엔진 터지거나 계약 어기면 job FAILED로. 예외 삼키지 말고 사유 기록
        job.status = "FAILED"
        job.failure_reason = str(exc)
    finally:
        job.completed_at = datetime.now(timezone.utc)