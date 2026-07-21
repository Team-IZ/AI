"""B1(확정) 공유 API 키 인증 동작 고정 테스트.

계약:
- `internal_api_key`가 설정돼 있으면 보호 라우트는 `X-Internal-Key` 헤더를 요구한다.
- 키가 비어 있으면 검증 비활성 (standalone 모드의 목업 프론트 호출자를 위한 기본값).
- `/api/health`는 운영 모니터링용이므로 어떤 경우에도 인증 없이 200.
"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import INTERNAL_KEY_HEADER, require_internal_key
from app.config import (
    API_V1_PREFIX,
    STANDALONE_DEV_API_KEY,
    Settings,
    get_settings,
)
from app.main import create_app

VALID_KEY = "test-shared-secret"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings는 lru_cache라 테스트 간 환경변수 변경이 안 먹는다."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app_with_protected_route(internal_api_key: str) -> FastAPI:
    """더미 보호 라우트를 붙인 앱.

    실제 업무 라우터(Phase 2의 /analyses 등)와 동일한 방식
    (API_V1_PREFIX + require_internal_key)으로 붙이되, 인증 계약 자체만 고립해
    검증하기 위해 업무 로직 없는 라우트를 쓴다. 실제 라우터에 인증이 걸려 있는지는
    test_analyses.py의 `test_requires_internal_key`가 확인한다.
    """
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(
        internal_api_key=internal_api_key
    )

    @app.get(f"{API_V1_PREFIX}/_probe", dependencies=[Depends(require_internal_key)])
    def _probe() -> dict:
        return {"ok": True}

    return app


def test_protected_route_rejects_missing_key():
    with TestClient(_app_with_protected_route(VALID_KEY)) as client:
        res = client.get(f"{API_V1_PREFIX}/_probe")
    assert res.status_code == 401
    # 명세 §2 공통 에러 형식
    error = res.json()["detail"]["error"]
    assert error["code"] == "INTERNAL_KEY_MISSING"
    assert error["retryable"] is False


def test_protected_route_rejects_wrong_key():
    with TestClient(_app_with_protected_route(VALID_KEY)) as client:
        res = client.get(
            f"{API_V1_PREFIX}/_probe", headers={INTERNAL_KEY_HEADER: "wrong-key"}
        )
    assert res.status_code == 401
    assert res.json()["detail"]["error"]["code"] == "INTERNAL_KEY_INVALID"


def test_protected_route_accepts_valid_key():
    with TestClient(_app_with_protected_route(VALID_KEY)) as client:
        res = client.get(
            f"{API_V1_PREFIX}/_probe", headers={INTERNAL_KEY_HEADER: VALID_KEY}
        )
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_validation_disabled_when_key_not_configured():
    """키 미설정 = 검증 비활성. standalone 목업 프론트가 그대로 호출할 수 있어야 한다."""
    with TestClient(_app_with_protected_route("")) as client:
        res = client.get(f"{API_V1_PREFIX}/_probe")
    assert res.status_code == 200


# --- 목업 전용 개발 키 vs 실제 통합 키 (B1 후속) ------------------------------
#
# 두 키는 용도가 다르고 유효 범위도 다르다:
# - STANDALONE_DEV_API_KEY: 공개 상수, standalone 전용. 목업 페이지가 쓴다.
# - INTERNAL_API_KEY: 실제 통합용 비밀 키(값 미정). integrated에서 강제된다.
# 아래 테스트들이 "개발 키는 integrated에서 절대 안 통한다"를 고정한다.

REAL_KEY = "real-integration-secret"
PROBE = f"{API_V1_PREFIX}/analyses/does-not-exist"


def _real_app(monkeypatch, mode: str, internal_api_key: str):
    """실제 앱·실제 라우터로 검증한다 (더미 라우트가 아니라 보호 대상 그 자체)."""
    monkeypatch.setenv("APP_MODE", mode)
    monkeypatch.setenv("INTERNAL_API_KEY", internal_api_key)
    get_settings.cache_clear()
    return create_app()


def test_dev_key_works_in_standalone(monkeypatch):
    """① standalone에서는 개발 키로 보호 라우트를 통과한다.

    실제 통합 키가 함께 설정돼 있어도(두 키 공존) 목업은 동작해야 한다.
    404(JOB_NOT_FOUND)는 인증을 통과해 라우터 본문까지 갔다는 뜻이다.
    """
    with TestClient(_real_app(monkeypatch, "standalone", REAL_KEY)) as client:
        res = client.get(PROBE, headers={INTERNAL_KEY_HEADER: STANDALONE_DEV_API_KEY})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "JOB_NOT_FOUND"


def test_dev_key_is_rejected_in_integrated_mode(monkeypatch):
    """② 핵심 불변식: integrated에서 개발 키는 거부된다."""
    with TestClient(_real_app(monkeypatch, "integrated", REAL_KEY)) as client:
        res = client.get(PROBE, headers={INTERNAL_KEY_HEADER: STANDALONE_DEV_API_KEY})
    assert res.status_code == 401
    assert res.json()["detail"]["error"]["code"] == "INTERNAL_KEY_INVALID"


def test_dev_key_is_rejected_in_integrated_even_without_real_key(monkeypatch):
    """②-b 실제 키가 비어 검증이 비활성인 상태에서도 개발 키만은 통과하지 못한다.

    "공개된 개발 키가 프로덕션 인증을 우회할 수 없다"를 설정값(INTERNAL_API_KEY의
    존재 여부)에 의존하지 않는 불변식으로 못 박는다.
    """
    with TestClient(_real_app(monkeypatch, "integrated", "")) as client:
        res = client.get(PROBE, headers={INTERNAL_KEY_HEADER: STANDALONE_DEV_API_KEY})
    assert res.status_code == 401


def test_real_key_works_in_integrated(monkeypatch):
    """③ 실제 키는 integrated에서 통과한다."""
    with TestClient(_real_app(monkeypatch, "integrated", REAL_KEY)) as client:
        res = client.get(PROBE, headers={INTERNAL_KEY_HEADER: REAL_KEY})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "JOB_NOT_FOUND"


def test_real_key_also_works_in_standalone(monkeypatch):
    """개발 키 도입이 기존 실제 키 경로를 깨지 않았는지 확인."""
    with TestClient(_real_app(monkeypatch, "standalone", REAL_KEY)) as client:
        res = client.get(PROBE, headers={INTERNAL_KEY_HEADER: REAL_KEY})
    assert res.status_code == 404


def test_mockup_page_uses_the_dev_key_constant():
    """목업 페이지의 하드코딩 값과 서버 상수가 어긋나면 목업이 조용히 401을 받는다.

    페이지가 키를 사용자에게 입력받지 않고 하드코딩하기로 한 선택의 안전장치다.
    """
    from app.main import TRAINEE_DIR

    html = (TRAINEE_DIR / "submission.html").read_text(encoding="utf-8")
    assert f'"{STANDALONE_DEV_API_KEY}"' in html


@pytest.mark.parametrize("mode", ["standalone", "integrated"])
def test_health_is_exempt_from_auth(monkeypatch, mode):
    """키가 설정된 integrated 모드에서도 health는 키 없이 200이어야 한다."""
    monkeypatch.setenv("APP_MODE", mode)
    monkeypatch.setenv("INTERNAL_API_KEY", VALID_KEY)
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["mode"] == mode
