""" 5축 후채점 API - 2개 엔드포인트 스텁. 비동기 job 패턴 """
from fastapi import APIRouter, BackgroundTasks, status

from app import gradings
from app.api.errors import ApiError
from app.schemas.common import ErrorResponse
from app.schemas.grading import GradingAccepted, GradingJobStatus, GradingRequest

router = APIRouter(tags=["gradings"])


@router.post(
    "/gradings", status_code=status.HTTP_202_ACCEPTED,
    response_model=GradingAccepted, summary="세션 transcript 5축 후채점 요청",
)
async def create_grading(body: GradingRequest, background_tasks: BackgroundTasks) -> GradingAccepted:
    """ 채점 요청 접수하고 즉시 202. 실제 채점은 백그라운드에서 """
    job = gradings.create_job(body)
    background_tasks.add_task(gradings.run_grading, job.job_id)
    return GradingAccepted(job_id=job.job_id, status="QUEUED")


@router.get(
    "/gradings/{job_id}", response_model=GradingJobStatus,
    summary="채점 상태·점수·근거 조회",
    responses={404: {"model": ErrorResponse, "description": "모르는 job_id"}},
)
async def get_grading(job_id: str) -> GradingJobStatus:
    job = gradings.get_job(job_id)
    if job is None:
        raise ApiError(
            status_code=404, error="JOB_NOT_FOUND",
            message=f"채점 job을 찾을 수 없습니다: {job_id}", retryable=False,
        )
    return job