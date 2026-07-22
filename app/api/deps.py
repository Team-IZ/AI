""" 라우터 공통으로 쓰는 의존성

FastAPI에서 '의존성'은 엔드포인트 실행 전에 먼저 돌려서
인증·검증 같은 공통 작업을 처리하는 함수
"""

from fastapi import Header

from app.api.errors import ApiError
from app.config import get_settings

def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """Spring→FastAPI 서비스 간 인증.

    파라미터 이름 x_internal_key 가 헤더 X-Internal-Key 로 자동 변환된다
    (언더스코어 → 하이픈). default=None 이라 헤더가 없어도 요청은 들어오고,
    없다는 사실을 이 함수가 판단한다.

    반환값이 없다. 통과시키는 게 목적이고, 실패는 예외로 끊는다.
    """
    expected = get_settings().internal_api_key
    
    # 키 미설정 = 로컬 개발. 검증 건너 뜀, get_settings 통해 production이 이 경로로 오는거 막음
    if not expected:
        return
    
    if x_internal_key != expected:
        # HTTPException 대신 ApiError를 던지기
        raise ApiError(
            status_code=401,
            error="UNAUTHORIZED",
            message="유효하지 않은 내부 키",
            retryable=False,
        )