""" app/main.py의 CORS 설정(D4, 2026-07-31) 테스트

GitHub Pages 테스트 페이지가 이 서비스를 브라우저에서 직접 호출하려면 필요하다.
allow_origins를 "*"로 열지 않고 특정 오리진만 허용하므로, 허용 대상과 차단 대상을
구분해 확인한다.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_allows_github_pages_origin():
    response = client.options(
        "/api/health",
        headers={
            "Origin": "https://team-iz.github.io",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "https://team-iz.github.io"


def test_allows_localhost_dev_origin():
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_rejects_unrelated_origin():
    response = client.options(
        "/api/health",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_x_internal_key_header_is_allowed():
    response = client.options(
        "/api/health",
        headers={
            "Origin": "https://team-iz.github.io",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Internal-Key",
        },
    )
    allowed = response.headers.get("access-control-allow-headers", "")
    assert "x-internal-key" in allowed.lower()


def test_credentials_are_not_allowed():
    """ 쿠키 안 씀 -- 헤더 기반 인증뿐이라 자격증명 공유가 필요 없다(D4) """
    response = client.options(
        "/api/health",
        headers={"Origin": "https://team-iz.github.io", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-credentials" not in response.headers
