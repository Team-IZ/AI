"""공용 라우터 의존성.

B1(확정): Spring→FastAPI 인증은 **공유 API 키**. Spring이 매 요청 헤더
`X-Internal-Key`에 비밀 문자열을 실어 보내고 FastAPI가 이를 검증한다.

미들웨어가 아니라 의존성으로 구현한 이유:
- 면제 경로를 경로 문자열 매칭으로 관리할 필요가 없다. `/api/health`는
  운영 모니터링용이라 인증 면제인데, 의존성 방식에서는 "붙이지 않는다"로 끝난다.
- Phase 2~4에서 `/api/v1` 라우터에 `dependencies=[Depends(require_internal_key)]`로
  일괄 적용하면 보호 범위가 코드에 명시적으로 드러난다.
"""
import secrets

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings

INTERNAL_KEY_HEADER = "X-Internal-Key"


def _unauthorized(code: str, message: str) -> HTTPException:
    """명세 §2 공통 에러 형식으로 401을 만든다.

    인증 실패는 재시도해도 같은 결과이므로 `retryable: false`.
    에러 코드 문자열은 B7(전체 목록 확정)에서 최종 확정 대상 — 잠정값이다.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": code, "message": message, "retryable": False}},
    )


def require_internal_key(
    x_internal_key: str | None = Header(default=None, alias=INTERNAL_KEY_HEADER),
    settings: Settings = Depends(get_settings),
) -> None:
    """`X-Internal-Key` 헤더를 검증한다.

    `internal_api_key`가 비어 있으면 검증을 **비활성화**한다. standalone 모드의
    호출자는 목업 프론트라 공유 키를 갖고 있지 않기 때문이며, 통합 배포에서는
    환경변수로 키를 설정하는 것이 활성화 조건이다.
    """
    expected = settings.internal_api_key
    if not expected:
        return  # 키 미설정 = 검증 비활성 (standalone 기본 동작)

    if not x_internal_key:
        raise _unauthorized(
            "INTERNAL_KEY_MISSING", f"{INTERNAL_KEY_HEADER} 헤더가 필요합니다"
        )

    # 타이밍 공격 방지를 위해 상수 시간 비교를 쓴다.
    if not secrets.compare_digest(x_internal_key, expected):
        raise _unauthorized("INTERNAL_KEY_INVALID", "내부 API 키가 올바르지 않습니다")
