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

from app.config import STANDALONE_DEV_API_KEY, Settings, get_settings

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

    받아들이는 키는 두 종류이고 **용도·유효 범위가 서로 다르다**:

    1. `STANDALONE_DEV_API_KEY` — 목업 전용 개발 키(공개 상수).
       **`app_mode == "standalone"`일 때만** 통한다. 아래 분기가 모드를 먼저
       확인하고, integrated에서는 이 상수를 아예 비교 대상으로 삼지 않는다.
       즉 값이 공개돼 있어도 프로덕션 인증을 우회할 수 없다.
       (standalone은 로컬 개발 도구 전용 모드라는 전제 위에 성립한다.)
    2. `settings.internal_api_key` — 실제 통합용 공유 키(B1). 비어 있으면
       검증 비활성(기존 동작 유지), 설정돼 있으면 integrated에서 강제된다.
    """
    # 개발 키는 standalone에서만. 이 모드 분기가 "개발 키는 프로덕션에서 안 통한다"의
    # 근거이므로 순서를 바꾸거나 조건을 완화하지 말 것
    # (tests/test_internal_auth.py::test_dev_key_is_rejected_in_integrated_mode가 고정).
    if x_internal_key and secrets.compare_digest(x_internal_key, STANDALONE_DEV_API_KEY):
        if settings.app_mode == "standalone":
            return
        # integrated에서는 실제 키 설정 여부와 무관하게 **명시적으로 거부**한다.
        # `internal_api_key`가 비어 있어 검증이 비활성인 상태에서도 개발 키만은
        # 통과시키지 않는다 — "공개된 개발 키가 프로덕션 인증을 우회할 수 없다"를
        # 설정값에 의존하지 않는 불변식으로 만들기 위해서다. 이 경로에 걸렸다면
        # standalone용 호출자가 integrated 서버를 때린 설정 사고이므로 조용히
        # 통과시키는 것보다 401로 드러내는 편이 낫다.
        raise _unauthorized(
            "INTERNAL_KEY_INVALID",
            "standalone 전용 개발 키는 integrated 모드에서 사용할 수 없습니다",
        )

    expected = settings.internal_api_key
    if not expected:
        return  # 실제 키 미설정 = 검증 비활성 (기존 동작 유지)

    if not x_internal_key:
        raise _unauthorized(
            "INTERNAL_KEY_MISSING", f"{INTERNAL_KEY_HEADER} 헤더가 필요합니다"
        )

    # 타이밍 공격 방지를 위해 상수 시간 비교를 쓴다.
    if not secrets.compare_digest(x_internal_key, expected):
        raise _unauthorized("INTERNAL_KEY_INVALID", "내부 API 키가 올바르지 않습니다")
