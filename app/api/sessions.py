""" 문답 세션 API(P03) - 4개 엔드포인트 스텁. 모두 동기(학생이 화면에서 대기) """
from fastapi import APIRouter, status

from app import sessions
from app.api.errors import ApiError
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
    responses={404: {"model": ErrorResponse, "description": "모르는 세션"}},
)
async def submit_answer(session_id: str, body: AnswerSubmit) -> SessionView:
    """ 답변 받고 다음 질문(또는 종료)를 돌려줌.
    
    같은 client_request_id 재전송 -> 처음 응답 그대로(멱등).
    세션 유실이면 404 -> Spring이 restore 호출
    """
    view = sessions.submit_answer(session_id, body)
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