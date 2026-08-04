""" 문답 세션 API(P03) — 엔드포인트 하나. 동기(학생이 화면에서 대기)

🔴 **무상태다** (2026-08-03, PLAN §T11). `POST /sessions`·`GET /sessions/{id}`·
`POST /sessions/{id}/restore`는 삭제됐다 — AI 메모리는 배포·재시작마다 날아가는데
세션을 들고 있으면 백엔드가 "시작 / 진행 / 404 복원" 세 갈래를 상시 경로로 구현해야 한다.
지금은 **매 요청이 곧 restore**라 한 갈래다.
"""
from fastapi import APIRouter, Header

from app import sessions
from app.api.errors import ApiError
from app.engines.analysis.stages import StageError
from app.schemas.common import ErrorResponse
from app.schemas.session import AnswerResult, AnswerSubmit

router = APIRouter(tags=["sessions"])


@router.post(
    "/sessions/{session_id}/answers", response_model=AnswerResult,
    summary="답변 제출 -> 채점 + 다음 질문 (P03)",
    responses={
        503: {"model": ErrorResponse,
              "description": "채점 실패. 같은 clientRequestId로 재전송하면 된다"},
    },
)
def submit_answer(
    session_id: str, body: AnswerSubmit,
    x_trace_id: str | None = Header(default=None, description="분산 추적 ID"),
) -> AnswerResult:
    """답변을 채점하고 다음 질문(또는 종료)을 돌려준다.

    문제·기록·커서는 요청에 실려 온다. 같은 client_request_id 재전송 -> 처음 응답 그대로(멱등).

    🔴 **`async def`가 아니라 `def`여야 한다.** 이 경로는 세션에서 유일하게 LLM을
    호출하는데(`grading.grade`), vendor 클라이언트가 `urllib.request` 기반의 블로킹
    호출이다. `async def`로 두면 채점 4.5~7.7초 동안 **이벤트 루프가 통째로 멈춰**
    다른 학생의 채점은 물론 폴링·헬스체크까지 대기한다(동시 처리 1명).
    `def`면 FastAPI가 스레드풀에서 돌려 동시 40건까지 처리한다.
    LLM 호출을 비동기로 바꾸기 전까지 이 시그니처를 되돌리지 않는다.
    """
    try:
        return sessions.submit_answer(session_id, body, trace_id=x_trace_id)
    except StageError as exc:
        # 🔴 채점은 세션에서 유일한 LLM 호출이고, 무료 티어 실패율이 32%다.
        # 안 잡으면 처리되지 않은 500이 나가는데 **본문이 비어 있어 프론트가
        # 파싱조차 못 한다** — 학생 화면에 아무 안내도 못 띄운다(2026-08-02 실측).
        #
        # 이 턴은 기록되지 않았으므로 **같은 clientRequestId로 재전송하면 된다.**
        # 멱등키가 같아 중복 턴이 되지 않는다. 그래서 retryable=True다.
        raise ApiError(
            status_code=503, error="GRADING_UNAVAILABLE",
            message=f"채점에 실패했습니다. 같은 clientRequestId로 다시 보내주세요: {exc}",
            retryable=True,
        ) from exc
