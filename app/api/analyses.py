""" 분석 API (P02) - POST /api/v0/analyses """
import uuid

from fastapi import APIRouter, Header, status

from app.schemas.analysis import AnalysisAccepted, AnalysisRequest
from app.schemas.common import ErrorResponse

router = APIRouter(tags=["analyses"])

# 멱등성 키 -> job_id
# 같은 키로 다시 오면 새 job을 만들지 않고 처음 만든 id를 돌려줌
# Redis 도입 시 교체하고 job 저장소와 합침.
_job_id_by_idempotency_key: dict[str, str] = {}


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAccepted,
    summary="코드 분석 요청 (P02)",
    responses={422: {"model": ErrorResponse, "description": "요청 스키마 위반"}},
)
async def create_analysis(
    body: AnalysisRequest,
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
    if idempotency_key and idempotency_key in _job_id_by_idempotency_key:
        # 같은 요청 다시 온 경우 새로 만들지 않고 처음 것 돌려주기.
        return AnalysisAccepted(
            job_id=_job_id_by_idempotency_key[idempotency_key], status="QUEUED"
        )
        
    job_id = str(uuid.uuid4())
    if idempotency_key:
        _job_id_by_idempotency_key[idempotency_key] = job_id
    
    return AnalysisAccepted(job_id=job_id, status="QUEUED")