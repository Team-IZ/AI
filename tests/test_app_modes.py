"""모드별 앱 조립 동작 고정 테스트 (PLAN §1.5, 명세 §1).

- CORS는 standalone 모드에서만 붙는다: integrated의 호출자는 Spring뿐이고
  React는 FastAPI를 직접 호출하지 않으므로 브라우저 preflight 경로가 없다(명세 §1).
- standalone은 목업 프론트(trainee/, shared/)를 정적 서빙한다.
"""
import io
import json
import re
import zipfile

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.config import API_V1_PREFIX, get_settings
from app.main import _AI_ROOT, create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache(tmp_path, monkeypatch):
    # 목업 제출 흐름 테스트가 실제 분석을 돌리므로 작업공간을 격리한다 (§3.3).
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("INTERNAL_API_KEY", "")
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
        assert client.get("/shared/session-state.js").status_code == 404
        assert client.get("/submission.html").status_code == 404


def test_standalone_mode_serves_static_mockup(monkeypatch):
    monkeypatch.setenv("APP_MODE", "standalone")
    with TestClient(create_app()) as client:
        assert client.get("/shared/session-state.js").status_code == 200
        assert client.get("/submission.html").status_code == 200


def test_pyodide_analysis_path_is_deleted():
    """Phase 2b: 두 구현 병존 금지(PLAN §3 공통 원칙).

    submission.html이 FastAPI 분석 API를 호출하게 되면서 브라우저 Pyodide 경로는
    통째로 삭제됐다. 되살아나면(파일 복구, script 태그 재추가) 이 테스트가 알린다.
    """
    assert not (_AI_ROOT / "webtool_driver.py").exists()
    assert not (_AI_ROOT / "shared" / "p02-engine.js").exists()

    page = (_AI_ROOT / "trainee" / "submission.html").read_text(encoding="utf-8")
    assert "<script src=" in page  # 스크립트 태그 자체는 남아 있다(config/db/session-state)
    for dead in ("p02-engine.js", "pyodide.js", "pyodide-shared.js", "jszip"):
        assert f'src="{dead}' not in page and f"/{dead}" not in page, f"{dead} 참조가 남아 있다"
    # 데이터 출처가 FastAPI 분석 API여야 한다 (명세 §3)
    assert "/api/v1" in page and "/analyses" in page


def test_standalone_mockup_submission_flow_works(monkeypatch):
    """standalone 목업의 ZIP 제출 흐름이 실제로 동작함을 고정한다.

    Phase 2b 이전에는 이 자리에 strict xfail이 있었다(목업이 브라우저 Pyodide로
    `/webtool_driver.py`를 fetch하는데 그 경로가 서빙되지 않아 제출이 실패).
    이제 submission.html은 아래와 똑같은 요청을 FastAPI에 보낸다 — 즉 이 테스트가
    검증하는 코드 경로가 통합 시 Spring이 호출할 그 경로다(PLAN §3 공통 원칙).

    브라우저 JS 실행까지는 재현하지 못하므로, 페이지가 참조하는 API 경로 문자열이
    실제 라우트와 일치하는지도 함께 확인한다(수동 확인 대상 축소).
    """
    monkeypatch.setenv("APP_MODE", "standalone")
    app = create_app()

    page = (_AI_ROOT / "trainee" / "submission.html").read_text(encoding="utf-8")
    assert f'API_BASE = "{API_V1_PREFIX}"' in page, "페이지의 API base가 서버 prefix와 다르다"
    # 라우터가 중첩 객체로 감싸여 app.routes에 평면적으로 안 보이므로 OpenAPI로 확인한다.
    paths = app.openapi()["paths"]
    assert f"{API_V1_PREFIX}/analyses" in paths
    assert f"{API_V1_PREFIX}/analyses/{{job_id}}" in paths

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("src/danger.py", 'API_KEY = "sk-abcdef1234567890abcdef1234567890"\n')
        zf.writestr("src/main.py", "import danger\n\nprint(danger.API_KEY)\n")

    with TestClient(app) as client:
        # 페이지가 보내는 것과 동일한 multipart 형태 (payload JSON + file)
        res = client.post(
            f"{API_V1_PREFIX}/analyses",
            data={
                "payload": json.dumps(
                    {"extraction_scope": "TOTAL", "question_budget": 4, "method": "ZIP_WITH_GITLOG"}
                )
            },
            files={"file": ("submission.zip", buf.getvalue(), "application/zip")},
        )
        assert res.status_code == 202, res.text
        job_id = res.json()["job_id"]

        for _ in range(50):
            body = client.get(f"{API_V1_PREFIX}/analyses/{job_id}").json()
            if body["status"] in ("READY", "PARTIAL", "FAILED"):
                break
        assert body["status"] == "READY", body

    findings = body["result"]["findings"]
    assert findings, "목업이 렌더링할 finding이 최소 1건 나와야 한다"
    # 페이지의 buildFindingsBasket()이 읽는 필드가 실제로 채워져 있어야 한다
    for f in findings:
        assert f["finding_id"] and f["summary"] is not None
    assert any(f["code_context"] and f["code_context"]["snippet"] for f in findings)


def test_static_pages_referenced_assets_exist():
    """목업 3개 페이지가 참조하는 로컬 정적 자원이 실제로 존재하는지 고정한다.

    Phase 2b에서 script 태그를 들어냈으므로, 남은 참조에 죽은 경로가 없음을 확인한다.
    (외부 CDN은 대상에서 제외 — 네트워크 의존.)
    """
    import re

    pattern = re.compile(r'(?:src|href)="(\.\./[^"]+|[^":]+\.(?:js|css|html|json))"')
    missing = []
    for page in ("submission.html", "session.html", "result.html"):
        text = (_AI_ROOT / "trainee" / page).read_text(encoding="utf-8")
        for ref in pattern.findall(text):
            target = (_AI_ROOT / "trainee" / ref).resolve()
            if not target.exists():
                missing.append(f"{page} -> {ref}")
    assert not missing, f"참조하지만 존재하지 않는 정적 자원: {missing}"
