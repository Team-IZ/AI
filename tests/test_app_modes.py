"""모드별 앱 조립 동작 고정 테스트 (PLAN §1.5, 명세 §1).

- CORS는 standalone 모드에서만 붙는다: integrated의 호출자는 Spring뿐이고
  React는 FastAPI를 직접 호출하지 않으므로 브라우저 preflight 경로가 없다(명세 §1).
- standalone은 목업 프론트(trainee/, shared/)를 정적 서빙한다.
"""
import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import _AI_ROOT, create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _has_cors(app) -> bool:
    return any(m.cls is CORSMiddleware for m in app.user_middleware)


def test_integrated_mode_has_no_cors_middleware(monkeypatch):
    monkeypatch.setenv("APP_MODE", "integrated")
    assert _has_cors(create_app()) is False


def test_standalone_mode_has_cors_middleware(monkeypatch):
    monkeypatch.setenv("APP_MODE", "standalone")
    assert _has_cors(create_app()) is True


def test_integrated_mode_does_not_serve_static_mockup(monkeypatch):
    """integrated에서는 목업 프론트를 서빙하지 않는다."""
    monkeypatch.setenv("APP_MODE", "integrated")
    with TestClient(create_app()) as client:
        assert client.get("/shared/p02-engine.js").status_code == 404


def test_standalone_mode_serves_static_mockup(monkeypatch):
    monkeypatch.setenv("APP_MODE", "standalone")
    with TestClient(create_app()) as client:
        res = client.get("/shared/p02-engine.js")
    assert res.status_code == 200


def test_webtool_driver_is_still_required_by_mockup():
    """webtool_driver.py 삭제 금지 사실을 고정한다.

    로직은 app/core/pipeline_runner.py로 이관됐지만, shared/p02-engine.js가 런타임에
    이 파일을 fetch해 Pyodide FS에 써넣으므로 파일 자체는 아직 지울 수 없다.
    Phase 2에서 submission.html을 FastAPI API로 연결하며 Pyodide 경로를 제거할 때
    이 테스트도 함께 없앤다.

    NOTE: 현재 FastAPI 정적 서빙으로는 이 fetch가 실제로는 404다 — 상세는
    test_webtool_driver_fetch_path_is_not_served_yet 참고.
    """
    driver = _AI_ROOT / "webtool_driver.py"
    assert driver.is_file(), "shared/p02-engine.js가 아직 이 파일을 fetch한다"

    engine = (_AI_ROOT / "shared" / "p02-engine.js").read_text(encoding="utf-8")
    assert 'fetch("../webtool_driver.py")' in engine


@pytest.mark.xfail(
    reason=(
        "Phase 2 미구현: trainee/가 '/'에 마운트돼 있어 목업 페이지(/submission.html)의 "
        "fetch('../webtool_driver.py')가 /webtool_driver.py로 해석되지만 그 경로는 "
        "서빙되지 않는다. 즉 현재 standalone 목업의 제출 흐름은 화면만 뜨고 실패한다. "
        "Phase 2에서 submission.html을 FastAPI 분석 API 호출로 교체하며 해소(Pyodide 경로 삭제)."
    ),
    strict=True,
)
def test_webtool_driver_fetch_path_is_not_served_yet(monkeypatch):
    monkeypatch.setenv("APP_MODE", "standalone")
    with TestClient(create_app()) as client:
        assert client.get("/webtool_driver.py").status_code == 200
