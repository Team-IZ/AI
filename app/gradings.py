""" 채점 job의 인메모리 저장소 + 스텁 

인메모리 dict - 재시작시 유실. 스케일 필요 시 Redis/DB로 이전
스텁이라 고정 점수 돌려줌. 실제 5축 LLM-as-Judge는 9단계에 이식
"""
import uuid
from datetime import datetime, timezone
from typing import get_args

from app.schemas.grading import(
    AxisCode,
    GradingJobStatus,
    GradingRequest,
    GradingResult,
)

# job_id -> 채점 상태 결과
_jobs: dict[str, GradingJobStatus] = {}

def get_job(job_id: str) -> GradingJobStatus | None:
    return _jobs.get(job_id)

def create_job(body: GradingRequest) -> GradingJobStatus:
    """ QUEUED 채점 job 생성. 아직 채점 안 함 """
    job = GradingJobStatus(
        job_id=str(uuid.uuid4()),
        session_id=body.session_id,
        status="QUEUED",
    )
    _jobs[job.job_id] = job
    return job

def _stub_result() -> GradingResult:
    """ 고정 5축 결과. 백엔드가 파싱 코드 짤 수 있도록 실제 모양 주기 """
    # get_args(AxisCode) = 5개 축 코드 튜플. Literal에서 값들 꺼냄.
    # 일단 다 4점 주기
    axes = [
        {
            "axis_code": code,
            "score": 4,
            "evidence": [{"turn_ref": 1, "quote_text": "[stub]", "reason": "[stub] 채점 근거"}]
        }
        for code in get_args(AxisCode)
    ]
    total = sum(a["score"] for a in axes)       # 4 * 5 = 20 
    return GradingResult.model_validate(
        {
            "axis_scores": axes,
            "total_score": total,
            "average_score": total / len(axes),
            "versions": {"model_code": "stub-0", "prompt_version": "stub-0", "rubric_version": "stub-0"},
        }
    )
    
def run_grading(job_id: str) -> None:
    """ 백그라운드 워커. QUEUED -> GRADING -> COMPLETED(스텁은 항상 성공) """
    job = _jobs[job_id]
    job.status = "GRADING"
    job.started_at = datetime.now(timezone.utc)
    
    try:
        job.result = _stub_result()
        job.status = "COMPLETED"
    except Exception as exc:
        job.status = "FAILED"
        job.failure_reason = str(exc)
    finally:
        job.completed_at = datetime.now(timezone.utc)