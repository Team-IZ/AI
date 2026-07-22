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
    
    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """pydantic 검증 실패를 계약 형태로
        exc.errors()는 실패 목록. 여러 개여도 첫 번째만 메시지로 사용
        호출자(Spring)이기에 전체 목록 필요 없음(사람이 굳이 볼 이유 없기에)
        """
        first = exc.errors()[0]
        # loc = ("body", "source", "repoUrl") 같은 경로. 맨 앞 "body"는 빼고 이어붙이기
        where = ".".join(str(part) for part in first["loc"][1:])
        # 본문 자체가 JSON이 아니면 loc이 필드 경로가 아니라 문자 위치라 의미가 없다
        message = f"{where}: {first['msg']}" if first["type"] != "json_invalid" else first["msg"]
        return JSONResponse(
            status_code=422,
            content=_body("INVALID_REQUEST", message, False),
        )
