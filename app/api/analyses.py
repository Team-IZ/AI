""" 분석 API (P02) - POST /api/v0/analyses """
import json
import uuid
from typing import Any

# Request — 원시 요청 객체. Content-Type·본문·폼에 직접 접근할 수 있다.
#           스키마 자동 바인딩을 포기하는 대신 분기를 제어한다.
from fastapi import APIRouter, Header, Request, status

# ValidationError — pydantic이 검증 실패 시 던지는 예외.
#                   자동 바인딩이 아니라 직접 검증.
from pydantic import ValidationError

from app.api.errors import ApiError, format_validation_message
from app.schemas.analysis import AnalysisAccepted, AnalysisRequest
from app.schemas.common import ErrorResponse

router = APIRouter(tags=["analyses"])

# 멱등성 키 -> job_id
# 같은 키로 다시 오면 새 job을 만들지 않고 처음 만든 id를 돌려줌
# Redis 도입 시 교체하고 job 저장소와 합침.
_job_id_by_idempotency_key: dict[str, str] = {}

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

# Swagger에서 두 Content-Type을 모두 시험할 수 있게 requestBody를 직접 기술한다.
# 자동 바인딩(body: AnalysisRequest)을 포기했으므로 FastAPI가 스키마를 못 만든다.
# 자동 바인딩을 포기하면 FastAPI가 AnalysisRequest를 components/schemas에 넣지 않아
# $ref가 깨진다. 모델에서 직접 JSON 스키마를 뽑아 인라인으로 박는다.
# ref_template로 중첩 모델(AnalysisSource)까지 $defs 안에 함께 실린다.
_ANALYSIS_REQUEST_SCHEMA = AnalysisRequest.model_json_schema(
    ref_template="#/components/schemas/AnalysisRequest/$defs/{model}"
)

_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": _ANALYSIS_REQUEST_SCHEMA,
            "example": {
                "method": "GITHUB_URL",
                "source": {"repoUrl": "https://github.com/owner/repo", "branch": "main"},
                "extractionScope": "TOTAL",
                "questionBudget": 4,
            },
        },
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["payload", "file"],
                "properties": {
                    "payload": {
                        "type": "string",
                        "description": "요청 JSON을 문자열로. method는 ZIP_WITH_GITLOG",
                        "example": '{"method":"ZIP_WITH_GITLOG","extractionScope":"TOTAL"}',
                    },
                    "file": {"type": "string", "format": "binary", "description": "제출 ZIP"},
                },
            }
        },
    },
}

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
    # 파라미터명 -> 헤더명 자동 변환(언더스코어 -> 하이픈, 첫글자 대문자로)
    idempotency_key: str | None = Header(
        default=None, description="submissionId:attemptNo. 중복 요청 판별"
    ),
    x_trace_id: str | None = Header(default=None, description="분산 추적 ID"),
) -> AnalysisAccepted:
    """ 분석 요청 접수하고 즉시 202 반환 
    
    지금 스텁이라 job_id만 발급.
    
    x_trace_id는 아직 사용 X. 헤더 자리 게약 고정하고 Swagger 노출 위해 지금 받아둠
    - 로깅에 붙이는 것은 나중
    """
    payload, zip_bytes = await _read_request(request)
    body = _validate(payload)
    
    # method와 실제 전송 형태 어긋나면 여기서 잡음
    # 스키마만으로 표현할 수 없음 - Content-Type은 본문 밖의 정보
    if body.method == "ZIP_WITH_GITLOG" and not zip_bytes:
        raise _invalid("method=ZIP_WITH_GITLOG는 multipart/form-data로 ZIP을 함께 보내야 합니다")
        
    if idempotency_key and idempotency_key in _job_id_by_idempotency_key:
        # 같은 요청 다시 온 경우 새로 만들지 않고 처음 것 돌려주기.
        return AnalysisAccepted(
            job_id=_job_id_by_idempotency_key[idempotency_key], status="QUEUED"
        )
        
    job_id = str(uuid.uuid4())
    if idempotency_key:
        _job_id_by_idempotency_key[idempotency_key] = job_id
    
    return AnalysisAccepted(job_id=job_id, status="QUEUED")