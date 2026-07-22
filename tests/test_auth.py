from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_health_is_exempt_from_auth():
    """헬스 체크는 키 없이도 통과해야 한다(운영 모니터링용)."""
    response = client.get("/api/health")

    assert response.status_code == 200