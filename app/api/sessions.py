""" 문답 세션 API(P03) - 4개 엔드포인트 스텁. 모두 동기(학생이 화면에서 대기) """
from fastapi import APIRouter, status

from app import sessions
from app.api.errors import ApiError
from app.engines.analysis.stages import StageError
from app.schemas.common import ErrorResponse
from app.schemas.session import AnswerSubmit, SessionRestore, SessionStart, SessionView

router = APIRouter(tags=["sessions"])


def _not_found(session_id: str) -> ApiError:
    # 세션 유실 -> Spring이 restore을 호출해야 하므로 재시도 무의미(retryable=False)
    return ApiError(
        status_code=404, error="SESSION_NOT_FOUND",
        message=f"세션을 찾을 수 없습니다: {session_id}", retryable=False,
    )
    

@router.post(
    "/sessions", status_code=status.HTTP_201_CREATED,
    response_model=SessionView, summary="검증 세션 시작 (P03)",
)
async def start_session(body: SessionStart) -> SessionView:
    """ 세션 만들고 첫 질문 돌려주기 """
    return sessions.start_session(body)


@router.post(
    "/sessions/{session_id}/answers", response_model=SessionView,
    summary="답변 제출 -> 다음 질문/종료 (P03)",
    responses={
        404: {"model": ErrorResponse, "description": "모르는 세션"},
        503: {"model": ErrorResponse,
              "description": "채점 실패. 같은 clientRequestId로 재전송하면 된다"},
    },
)
def submit_answer(session_id: str, body: AnswerSubmit) -> SessionView:
    """ 답변 받고 다음 질문(또는 종료)를 돌려줌.

    같은 client_request_id 재전송 -> 처음 응답 그대로(멱등).
    세션 유실이면 404 -> Spring이 restore 호출

    🔴 **`async def`가 아니라 `def`여야 한다.** 이 경로는 세션에서 유일하게 LLM을
    호출하는데(`grading.grade`), vendor 클라이언트가 `urllib.request` 기반의 블로킹
    호출이다. `async def`로 두면 채점 4.5~7.7초 동안 **이벤트 루프가 통째로 멈춰**
    다른 학생의 채점은 물론 폴링·헬스체크까지 대기한다(동시 처리 1명).
    `def`면 FastAPI가 스레드풀에서 돌려 동시 40건까지 처리한다.
    LLM 호출을 비동기로 바꾸기 전까지 이 시그니처를 되돌리지 않는다.
    """
    try:
        view = sessions.submit_answer(session_id, body)
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

    if view is None:
        raise _not_found(session_id)
    return view


@router.get(
    "/sessions/{session_id}", response_model=SessionView,
    summary="세션 상태 조회 (P03)",
    responses={404: {"model": ErrorResponse, "description": "모르는 세션"}},
)
async def get_session(session_id: str) -> SessionView:
    """ 세션 현재 상태, 질문 반환(Spring 풀링/복구용) """
    view = sessions.get_session(session_id)
    if view is None:
        raise _not_found(session_id)
    return view


@router.post(
    "/sessions/{session_id}/restore", response_model=SessionView,
    summary="유실 세션 복원(P03)",
)
async def restore_session(session_id: str, body: SessionRestore) -> SessionView:
    """ Spring이 저장해둔 transcript로 유실 세션 재구성하고 이어질 질문 반환 """
    return sessions.restore_session(session_id, body)