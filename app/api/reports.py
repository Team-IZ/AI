""" 보고서 API — 2개 엔드포인트 스텁. 분석과 같은 비동기 job 패턴 """
from fastapi import APIRouter, BackgroundTasks, status

from app import reports
from app.api.errors import ApiError
from app.schemas.common import ErrorResponse
from app.schemas.report import ReportAccepted, ReportJobStatus, ReportRequest

router = APIRouter(tags=["reports"])


@router.post(
    "/reports", status_code=status.HTTP_202_ACCEPTED,
    response_model=ReportAccepted, summary="세션 결과로 보고서 생성 요청",
)
async def create_report(body: ReportRequest, background_tasks: BackgroundTasks) -> ReportAccepted:
    """요청을 접수하고 즉시 202. 실제 생성은 백그라운드에서."""
    job = reports.create_job(body)
    background_tasks.add_task(reports.run_report, job.job_id, body)
    return ReportAccepted(job_id=job.job_id, status="QUEUED")


@router.get(
    "/reports/{job_id}", response_model=ReportJobStatus,
    summary="보고서 상태·점수 매트릭스·교안 참조 조회",
    responses={404: {"model": ErrorResponse, "description": "모르는 job_id"}},
)
async def get_report(job_id: str) -> ReportJobStatus:
    job = reports.get_job(job_id)
    if job is None:
        raise ApiError(
            status_code=404, error="JOB_NOT_FOUND",
            message=f"보고서 job을 찾을 수 없습니다: {job_id}", retryable=False,
        )
    return job
