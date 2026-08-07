""" 보고서 API — 2개 엔드포인트 스텁. 분석과 같은 비동기 job 패턴 """
from fastapi import APIRouter, BackgroundTasks, Header, status

from app import reports
from app.api.errors import ApiError
from app.schemas.common import ErrorResponse
from app.schemas.report import ReportAccepted, ReportJobStatus, ReportRequest

router = APIRouter(tags=["reports"])


@router.post(
    "/reports", status_code=status.HTTP_202_ACCEPTED,
    response_model=ReportAccepted, summary="세션 결과로 보고서 생성 요청",
)
async def create_report(
    body: ReportRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(
        default=None, description="{problemId}:{scoreRunId}. 중복 요청 판별"
    ),
    x_trace_id: str | None = Header(default=None, description="분산 추적 ID"),
) -> ReportAccepted:
    """요청을 접수하고 즉시 202. 실제 생성은 백그라운드에서.

    같은 멱등키가 다시 오면 처음 jobId를 그대로 돌려주고 **LLM을 다시 부르지 않는다.**
    보고서는 문제마다 1건이라 중복이 곧 비용이다(2026-08-03, PLAN §T11 D-2).
    """
    if idempotency_key:
        # D-fix (redteam audit H12 companion): problem_id 불일치는 409로 옮긴다.
        try:
            existing = reports.job_id_for_key(idempotency_key, body.problem_id)
        except ValueError as exc:
            raise ApiError(status_code=409, error="IDEMPOTENCY_CONFLICT", message=str(exc)) from exc
        if existing:
            return ReportAccepted(job_id=existing, status="QUEUED")

    job = reports.create_job(body, idempotency_key)
    # 헤더 2개는 ai_usage 원장에 그대로 실린다 — Spring이 과금·추적을 이 값으로 잇는다.
    background_tasks.add_task(reports.run_report, job.job_id, body,
                              idempotency_key=idempotency_key, trace_id=x_trace_id)
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
