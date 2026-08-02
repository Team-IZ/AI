""" 분석 API (P02) - POST /api/v0/analyses """
import json
from typing import Any

# Request — 원시 요청 객체. Content-Type·본문·폼에 직접 접근할 수 있다.
#           스키마 자동 바인딩을 포기하는 대신 분기를 제어한다.
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
# ValidationError — pydantic이 검증 실패 시 던지는 예외.
#                   자동 바인딩이 아니라 직접 검증.
from pydantic import ValidationError

from app import jobs
from app.api.errors import ApiError, format_validation_message
from app.api.multipart_docs import multipart_body
from app.schemas.analysis import (
    AnalysisAccepted,
    AnalysisJobStatus,
    AnalysisRequest,
)
from app.schemas.common import ErrorResponse

from app.engines import get_analysis_engine
from app.engines.base import AnalysisEngine

router = APIRouter(tags=["analyses"])

def _invalid(message: str) -> ApiError:
    return ApiError(status_code=422, error="INVALID_REQUEST", message=message)

async def _read_request(request: Request) -> tuple[dict[str,Any], bytes | None]:
    """Content-Type을 보고 본문을 읽는다. (요청 dict, ZIP 바이트) 를 돌려준다.

    multipart는 폼 필드 payload(JSON 문자열) + file(ZIP)로 온다.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    
    if content_type == "multipart/form-data":
        form = await request.form()
        raw_payload = form.get("payload")
        upload = form.get("file")
        # upload가 str이면 파일이 아니라 문자열 필드로 온 것
        if raw_payload is None or upload is None or isinstance(upload, str):
            raise _invalid("multipart 요청에는 payload와 file이 모두 필요합니다")
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError) as exc:
            raise _invalid("payload가 올바른 JSON이 아닙니다") from exc
        return payload, await upload.read()
    
    try:
        payload = await request.json()
    except (TypeError, ValueError) as exc:
        raise _invalid("요청 본문이 올바른 JSON이 아닙니다") from exc
    return payload, None

def _validate(payload: Any) -> AnalysisRequest:
    """ dict를 스키마로 검증. 자동 바인딩을 안 쓰므로 직접 부르기 """
    if not isinstance(payload, dict):
        raise _invalid("요청 본문은 객체여야 합니다")
    try:
        return AnalysisRequest.model_validate(payload)
    except ValueError as exc:
        raise _invalid(format_validation_message(exc.errors())) from exc

# 자동 바인딩을 포기해 AnalysisRequest가 components에 안 잡힌다 — 스펙에 직접 실어야
# 백엔드가 요청 필드를 볼 수 있다. 방식·근거는 multipart_docs 모듈 주석 참고.
_REQUEST_BODY = multipart_body(
    AnalysisRequest,
    file_description="제출 ZIP",
    payload_example='{"method":"ZIP_WITH_GITLOG","extractionScope":"TOTAL"}',
    json_example={
        "method": "GITHUB_URL",
        "source": {"repoUrl": "https://github.com/owner/repo", "branch": "main"},
        "extractionScope": "TOTAL",
        "questionBudget": 3,
    },
)

@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAccepted,
    summary="코드 분석 요청 (P02)",
    responses={422: {"model": ErrorResponse, "description": "요청 스키마 위반"}},
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def create_analysis(
    request: Request,
    # BackgroundTasks: FastAPI가 자동 주입. 여기 add_task 한 작업은
    # 응답을 보낸 "뒤"에 실행된다 → 202를 먼저 돌려주고 분석은 나중.
    background_tasks: BackgroundTasks,
    engine: AnalysisEngine = Depends(get_analysis_engine),
    
    # 파라미터명 -> 헤더명 자동 변환(언더스코어 -> 하이픈, 첫글자 대문자로)
    idempotency_key: str | None = Header(
        default=None, description="submissionId:attemptNo. 중복 요청 판별"
    ),
    x_trace_id: str | None = Header(default=None, description="분산 추적 ID"),
) -> AnalysisAccepted:
    """ 분석 요청 접수하고 즉시 202 반환. 실제 분석은 백그라운드
    
    x_trace_id는 아직 사용 X. 헤더 자리 게약 고정하고 Swagger 노출 위해 지금 받아둠
    - 로깅에 붙이는 것은 나중
    """
    payload, zip_bytes = await _read_request(request)
    body = _validate(payload)
    
    # method와 실제 전송 형태 어긋나면 여기서 잡음
    # 스키마만으로 표현할 수 없음 - Content-Type은 본문 밖의 정보
    if body.method == "ZIP_WITH_GITLOG" and not zip_bytes:
        raise _invalid("method=ZIP_WITH_GITLOG는 multipart/form-data로 ZIP을 함께 보내야 합니다")
        
    if idempotency_key:
        # 같은 요청 다시 온 경우 새로 만들지 않고 처음 것 돌려주기.
        existing = jobs.job_id_for_key(idempotency_key)
        if existing:
            return AnalysisAccepted(job_id=existing, status="QUEUED")
        
    job = jobs.create_job(body, idempotency_key)
    
    # 분석 백그라운드로 넘김. 이 줄은 즉시 반환, run_analysis는
    # 응답 전송 후 실행되어 QUEUED->RUNNING->SUCCEDED로 전이
    # 헤더 2개는 ai_usage 원장에 그대로 실린다 — Spring이 과금·추적을 이 값으로 잇는다.
    background_tasks.add_task(jobs.run_analysis, job.job_id, body, engine, zip_bytes,
                              idempotency_key=idempotency_key, trace_id=x_trace_id)

    # 202는 "접수했다"는 뜻이다. 실제 job이 이미 끝났는지는 별개이고,
    # 호출자는 GET으로 상태를 확인한다.
    return AnalysisAccepted(job_id=job.job_id, status="QUEUED")

@router.get(
    "/analyses/{job_id}",
    response_model=AnalysisJobStatus,
    summary="분석 상태·결과 조회 (P02)",
    responses={404: {"model": ErrorResponse, "description": "모르는 job_id"}},
)
async def get_analysis(job_id: str) -> AnalysisJobStatus:
    """분석 job의 현재 상태와 결과를 돌려준다.

    경로의 {job_id}가 함수 파라미터 job_id로 자동 연결된다. 이름이 같아야 한다.

    job 저장소가 인메모리라 프로세스가 재시작되면 404가 난다.
    그때는 Spring이 분석을 다시 요청하면 된다(FastAPI는 상태의 소유자가 아니다).
    """
    job = jobs.get_job(job_id)
    if job is None:
        raise ApiError(
            status_code=404,
            error="JOB_NOT_FOUND",
            message=f"분석 job을 찾을 수 없습니다: {job_id}",
            # 재시도해도 없는 건 없다. Spring은 재분석을 요청해야 한다.
            retryable=False,
        )
    return job