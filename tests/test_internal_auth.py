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
from app.config import API_V1_PREFIX, Settings, get_settings
from app.main import create_app

VALID_KEY = "test-shared-secret"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings는 lru_cache라 테스트 간 환경변수 변경이 안 먹는다."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app_with_protected_route(internal_api_key: str) -> FastAPI:
    """Phase 2 라우터를 흉내 낸 더미 보호 라우트를 붙인 앱.

    Phase 1에는 아직 보호 대상 엔드포인트가 없으므로, Phase 2~4가 붙일 위치
    (API_V1_PREFIX + require_internal_key)와 동일한 방식으로 테스트용 라우트를 만든다.
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
