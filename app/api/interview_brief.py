""" 면담 브리프 생성 API — 엔드포인트 하나. 동기(매니저가 화면에서 대기).

명세: `IZ-Get_면담브리프_생성API_명세서_v08.md`. `/sessions/{id}/answers`와 같은
이유로 `def`(async 아님) -- 이 경로의 유일한 LLM 호출(`interview_brief.generate`)이
`app.llm.client.chat()`을 거치는데 그게 blocking `urllib.request` 호출이다.
"""
from fastapi import APIRouter, Header

from app import interview_brief
from app.api.errors import ApiError, InterviewBriefError
from app.engines.analysis.stages import StageError
from app.schemas.common import ErrorResponse
from app.schemas.interview_brief import InterviewBriefRequest, InterviewBriefResponse
from app.usage import to_ai_usage

router = APIRouter(tags=["interview-brief"])

# 🔴 옛 D-ib2(사용량을 응답 헤더 X-Ai-Usage-* 7개 + 본문 usageMeta에 이중 전송)는
# 폐기됐다(2026-08-07). 백엔드 제안서가 다른 네 엔드포인트와 똑같이 **본문 aiUsage[]를
# ai_usage 테이블에 넣는다**고 명시했고 헤더 얘기는 한 줄도 없다 -- "와이어 모양이
# 미정이라 둘 다 채운다"는 전제가 사라졌다. 전용 UsageMeta 스키마도 같이 지웠다:
# 공용 AiUsage를 쓰면 featureCode·contextType·idempotencyKey처럼 백엔드가 원장에
# 넣어야 하는 값이 빠지지 않는다.


def _failure_code_for(exc: StageError) -> str:
    """StageError를 명세 §5.2의 8개 failureCode 중 하나로 매핑한다.

    LLM 전송 계층(client.py의 _classify)이 이미 TIMEOUT/RATE_LIMITED/PROVIDER_ERROR/
    CONTEXT_OVERFLOW 중 하나로 분류해 usages 마지막 행에 넣어둔다(status=FAILED일
    때). 이 engine이 직접 올린 검증 실패(개수·순서·interviewSourceId 위반)는 LLM
    호출 자체는 성공(status=SUCCEEDED)했는데 내용이 계약을 어긴 경우라 INVALID_JSON이
    가장 가까운 기존 값이다 -- 순수 문법 오류만 뜻하진 않지만, 이 8개 고정 어휘에
    "모델이 우리 계약을 어겼다"에 해당하는 다른 값이 없다.
    NO_AVAILABLE_MODEL_INSTANCE·MODEL_INSTANCE_QUOTA_EXHAUSTED·MODEL_CREDENTIAL_INVALID는
    다중 모델 인스턴스/자격증명 폴백 체계가 이 저장소에 아직 없어 도달 불가능하다.
    """
    if exc.usages and exc.usages[-1].get("status") == "FAILED":
        return exc.usages[-1].get("failure_code") or "PROVIDER_ERROR"
    return "INVALID_JSON"


@router.post(
    "/interview-brief:generate", response_model=InterviewBriefResponse,
    summary="면담 브리프 생성 (여는 말 + 질문 체크리스트)",
    responses={
        409: {"model": ErrorResponse,
              "description": "같은 idempotency-key로 다른 본문이 왔다. 재시도해도 같다"},
        503: {"description": "생성 실패. failureCode/message로 사유를 알려준다"},
    },
)
def generate_interview_brief(
    body: InterviewBriefRequest,
    # 헤더 이름은 나머지 네 엔드포인트와 같다(백엔드 제안서 1-1: `idempotency-key`·
    # `x-trace-id`). 옛 `X-Idempotency-Key`/`X-Request-Id`는 이 엔드포인트만의 변종이라
    # 폐기했다 -- 백엔드 클라이언트가 경로마다 헤더 이름을 갈아끼울 이유가 없다.
    #
    # 🔴 멱등키는 **필수**다(다른 넷은 선택). 이 경로만 contextId가 null이라
    # ai_usage.idempotency_key의 폴백이 트레이스까지 내려가는데, 그것도 없으면
    # `":INTERVIEW_BRIEF:1"`이 되어 **두 번째 호출이 전역 UNIQUE에 걸린다.**
    idempotency_key: str = Header(
        description="재시도 시 동일 값 재사용 -- 같으면 재계산 없이 그대로 반환. "
                    "ai_usage.idempotency_key의 요청 단위 키이기도 하다",
    ),
    x_trace_id: str | None = Header(default=None, description="분산 추적 ID"),
) -> InterviewBriefResponse:
    """여는 말 + 질문 체크리스트를 한 번의 LLM 호출로 만들어 즉시 반환한다.

    job/폴링 없음 -- 이 응답이 곧 결과다. 부분 성공 없음(§5.2): 검증에 하나라도
    걸리면 503 + failureCode로 전체 실패를 알린다.
    """
    try:
        result = interview_brief.generate(body, idempotency_key=idempotency_key)
    except ValueError as exc:
        # 같은 멱등키로 다른 본문이 왔다. /analyses의 H12 대응과 같은 409다 --
        # 생성 실패(503)가 아니라 호출 쪽 실수라 재시도해도 같은 결과다.
        raise ApiError(
            status_code=409, error="IDEMPOTENCY_CONFLICT", message=str(exc),
        ) from exc
    except StageError as exc:
        raise InterviewBriefError(
            status_code=503, failure_code=_failure_code_for(exc),
            message=f"면담 브리프 생성에 실패했습니다: {exc}",
        ) from exc

    return InterviewBriefResponse(
        opening_remark=result.opening_remark,
        items=[
            {
                "question_text": item.question_text,
                "question_rationale": item.question_rationale,
                "suggested_order": item.suggested_order,
                "interview_source_id": item.interview_source_id,
            }
            for item in result.items
        ],
        # contextId는 null이다 -- AI가 interview_brief.brief_id를 받은 적이 없다.
        # Spring이 저장 시점에 채우거나, 요청에 briefId를 실어주면 그때 채운다.
        ai_usage=to_ai_usage(
            result.usages, "INTERVIEW_BRIEF", None,
            feature_code="INTERVIEW_BRIEF_GENERATION",
            idempotency_key=idempotency_key, trace_id=x_trace_id,
        ),
    )
