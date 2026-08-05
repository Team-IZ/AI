""" 계약에 맞는 에러 응답 
FastAPI 기본 동작으로 계약 못맞춤
계약상 {error, message, retryable} 평탄화 구조라 예외 핸들러로 직접 만들기
"""

# fastapi.exceptions: FastAPI가 내부에서 던지는 예외들.
# RequestValidationError - pydantic 검증 실패 시 FastAPI가 던짐.
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

# fastapi.responses: 응답 객체들. JSONResponse는 상태코드와 본문 직접 지정
from fastapi.responses import JSONResponse

class ApiError(Exception):
    """ 우리 코드에서 던지는 에러. 아래 핸들러가 계약 형태로 변환 """

    def __init__(
        self, status_code: int, error: str, message: str, retryable: bool = False
    ) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        self.retryable = retryable


class InterviewBriefError(Exception):
    """면담 브리프 전용 에러. 명세서 §5.2 계약이 다른 4개 엔드포인트와 다르다 --
    {failureCode, message} 평탄 구조이고 retryable 필드가 없다(다른 뜻으로 별도
    계약이라 ApiError를 재사용하지 않는다)."""

    def __init__(self, status_code: int, failure_code: str, message: str) -> None:
        self.status_code = status_code
        self.failure_code = failure_code
        self.message = message


def _body(error: str, message: str, retryable: bool) -> dict:
    return {"error": error, "message": message, "retryable": retryable}

def register_error_handlers(app: FastAPI) -> None:
    """앱에 예외 핸들러 등록, main.py에서 한 번 호출"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.error, exc.message, exc.retryable)
        )

    @app.exception_handler(InterviewBriefError)
    async def _handle_interview_brief_error(request: Request, exc: InterviewBriefError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"failureCode": exc.failure_code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_body("INVALID_REQUEST", format_validation_message(exc.errors()), False),
        )
        

def format_validation_message(errors: list) -> str:
    """ pydantic 검증 실패 목록에서 메시지 한 줄 만들기.
    
    핸들러(자동 검증)와 라우터(수동 검증) 양쪽이 같은 형식을 내도록
    조립을 여기 한곳에 두기.
    """
    first = errors[0]
    # loc = ("body", "source", "repoUrl") 같은 경로. 맨 앞 "body"는 빼고 이어붙인다.
    #
    # 🔴 **무조건 [1:]로 자르면 안 된다.** 라우터가 손으로 검증하는 경로(multipart의
    # payload를 직접 파싱하는 /analyses·/curricula)는 loc에 "body"가 없어서
    # `("courseLabel",)`이 통째로 잘려 `": Field required"`가 나갔다 — **백엔드가 어느
    # 필드를 빠뜨렸는지 알 수 없는 메시지다**(2026-08-03 발견).
    loc = first["loc"]
    if loc and loc[0] in ("body", "query", "path", "header"):
        loc = loc[1:]
    where = ".".join(str(part) for part in loc)
    # 본문 자체가 JSON 아니면 loc이 필드 경로가 아니라 문자 위치라 의미가 없다
    if first["type"] == "json_invalid":
        return first["msg"]
    return f"{where}: {first['msg']}"