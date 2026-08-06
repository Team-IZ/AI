""" 교안 분석 API — POST /api/v0/curricula

PDF가 항상 필요하므로 multipart 하나만 받는다. analyses.py는 JSON·multipart 두 형태를
받아야 해서 손으로 파싱했지만, 여기는 FastAPI 기본 바인딩으로 충분하다.
"""
import json

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, UploadFile, status

from app import curricula
from app.api.errors import ApiError, format_validation_message
from app.api.multipart_docs import multipart_body
from app.schemas.common import ErrorResponse
from app.schemas.curriculum import (
    CurriculumAccepted,
    CurriculumJobStatus,
    CurriculumRequest,
)

router = APIRouter(tags=["curricula"])


def _invalid(message: str) -> ApiError:
    return ApiError(status_code=422, error="INVALID_REQUEST", message=message)


# PDF가 필수라 multipart만 문서화한다. payload의 구조를 스펙에 실어야
# 백엔드가 versionId·courseLabel 같은 필드를 볼 수 있다(multipart_docs 주석 참고).
_REQUEST_BODY = multipart_body(
    CurriculumRequest,
    file_description="교안 PDF",
    payload_example='{"versionId":"ver-1","courseLabel":"SQL"}',
    json_content=False,
)


@router.post(
    "/curricula",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CurriculumAccepted,
    summary="교안 분석 요청",
    responses={422: {"model": ErrorResponse, "description": "요청 스키마 위반"}},
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def create_curriculum(
    background_tasks: BackgroundTasks,
    payload: str = Form(
        description='요청 JSON을 문자열로. 예: {"versionId":"..."}',
    ),
    file: UploadFile = File(description="교안 PDF"),
    idempotency_key: str | None = Header(
        default=None, description="versionId:analysisVersion 등. 중복 요청 판별"
    ),
    x_trace_id: str | None = Header(default=None, description="분산 추적 ID"),
) -> CurriculumAccepted:
    """교안 분석을 접수하고 즉시 202를 돌려준다. 실제 추출은 백그라운드.

    교안 1개에 1~2분 이상 걸리므로 멱등키로 중복 실행을 막는다 — LMS가 재전송하면
    같은 PDF로 LLM을 두 번 돌리게 된다.
    """
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise _invalid("payload가 올바른 JSON이 아닙니다") from exc
    if not isinstance(parsed, dict):
        raise _invalid("payload는 객체여야 합니다")

    try:
        body = CurriculumRequest.model_validate(parsed)
    except ValueError as exc:
        raise _invalid(format_validation_message(exc.errors())) from exc

    # 잘못된 파일로 2분짜리 LLM 작업을 돌리지 않는다. 확장자가 아니라 선언된 타입을 본다.
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise _invalid(f"교안은 PDF여야 합니다: {file.content_type}")

    if idempotency_key:
        # D-fix (redteam audit H12 companion): version_id 불일치는 409로 옮긴다.
        try:
            existing = curricula.job_id_for_key(idempotency_key, body.version_id)
        except ValueError as exc:
            raise ApiError(status_code=409, error="IDEMPOTENCY_CONFLICT", message=str(exc)) from exc
        if existing:
            return CurriculumAccepted(job_id=existing, status="QUEUED")

    pdf_bytes = await file.read()
    job = curricula.create_job(body, idempotency_key)
    # 헤더 2개는 ai_usage 원장에 그대로 실린다 — Spring이 과금·추적을 이 값으로 잇는다.
    background_tasks.add_task(curricula.run_curriculum, job.job_id, body, pdf_bytes,
                              idempotency_key=idempotency_key, trace_id=x_trace_id)

    return CurriculumAccepted(job_id=job.job_id, status="QUEUED")


@router.get(
    "/curricula/{job_id}",
    response_model=CurriculumJobStatus,
    summary="교안 분석 상태·결과 조회",
    responses={404: {"model": ErrorResponse, "description": "모르는 job_id"}},
)
async def get_curriculum(job_id: str) -> CurriculumJobStatus:
    """교안 분석 job의 현재 상태와 결과를 돌려준다.

    저장소가 인메모리라 프로세스가 재시작되면 404가 난다. 그때는 Spring이
    다시 요청하면 된다(FastAPI는 상태의 소유자가 아니다).
    """
    job = curricula.get_job(job_id)
    if job is None:
        raise ApiError(
            status_code=404,
            error="JOB_NOT_FOUND",
            message=f"교안 분석 job을 찾을 수 없습니다: {job_id}",
            retryable=False,
        )
    return job