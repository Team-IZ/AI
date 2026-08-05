""" 면담 브리프 생성 API — 엔드포인트 하나. 동기(매니저가 화면에서 대기).

명세: `IZ-Get_면담브리프_생성API_명세서_v08.md`. `/sessions/{id}/answers`와 같은
이유로 `def`(async 아님) -- 이 경로의 유일한 LLM 호출(`interview_brief.generate`)이
`app.llm.client.chat()`을 거치는데 그게 blocking `urllib.request` 호출이다.
"""
from fastapi import APIRouter, Header, Response

from app import interview_brief
from app.api.errors import InterviewBriefError
from app.engines.analysis.stages import StageError
from app.schemas.interview_brief import InterviewBriefRequest, InterviewBriefResponse

router = APIRouter(tags=["interview-brief"])

# §7: AI가 제공하는 값만 여기 담는다(model_code/토큰 3종/latency/status/failureCode).
# feature_code·context_type·context_id·trigger_type·actor_user_id·request_id·
# idempotency_key는 전부 백엔드가 이미 아는 값이라 AI가 되돌려줄 필요가 없다.
#
# ⚠️ 확인 필요(계획 문서 참고): 명세 §7이 "응답 헤더 또는 본문 메타"라고만 적어
# 실제 와이어 모양이 미정이다. 잠정적으로 응답 헤더로 구현 -- 백엔드가 본문 필드를
# 원하면 이 부분만 바꾸면 된다(engine.generate()의 반환값은 그대로 재사용).
_USAGE_HEADER_PREFIX = "X-Ai-Usage-"


def _set_usage_headers(response: Response, usages: list[dict]) -> None:
    if not usages:
        return
    last = usages[-1]
    response.headers[f"{_USAGE_HEADER_PREFIX}Model-Code"] = str(last.get("model_code", ""))
    response.headers[f"{_USAGE_HEADER_PREFIX}Input-Tokens"] = str(last.get("input_token_count", 0))
    response.headers[f"{_USAGE_HEADER_PREFIX}Output-Tokens"] = str(last.get("output_token_count", 0))
    response.headers[f"{_USAGE_HEADER_PREFIX}Cached-Tokens"] = str(last.get("cached_token_count", 0))
    response.headers[f"{_USAGE_HEADER_PREFIX}Latency-Ms"] = str(last.get("latency_ms", 0))
    response.headers[f"{_USAGE_HEADER_PREFIX}Status"] = str(last.get("status", ""))
    if last.get("failure_code"):
        response.headers[f"{_USAGE_HEADER_PREFIX}Failure-Code"] = str(last["failure_code"])


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
        503: {"description": "생성 실패. failureCode/message로 사유를 알려준다"},
    },
)
def generate_interview_brief(
    body: InterviewBriefRequest, response: Response,
    # x_request_id: §7 표에 따르면 ai_usage.request_id는 백엔드가 아는 값이라
    # AI가 쓸 일이 없다 -- 계약 문서화 목적으로만 선언(openapi.json에 남긴다).
    x_request_id: str | None = Header(default=None, description="ai_usage.request_id로 그대로 잇는다"),
    x_idempotency_key: str | None = Header(default=None, description="재시도 시 동일 값 재사용 -- 같으면 재계산 없이 그대로 반환"),
) -> InterviewBriefResponse:
    """여는 말 + 질문 체크리스트를 한 번의 LLM 호출로 만들어 즉시 반환한다.

    job/폴링 없음 -- 이 응답이 곧 결과다. 부분 성공 없음(§5.2): 검증에 하나라도
    걸리면 503 + failureCode로 전체 실패를 알린다.
    """
    try:
        result = interview_brief.generate(body, idempotency_key=x_idempotency_key)
    except StageError as exc:
        raise InterviewBriefError(
            status_code=503, failure_code=_failure_code_for(exc),
            message=f"면담 브리프 생성에 실패했습니다: {exc}",
        ) from exc

    _set_usage_headers(response, result.usages)
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
    )
