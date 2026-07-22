from fastapi.testclient import TestClient
from app.main import app

# 서버 띄우지 않고 앱 직접 호출 -> 포트 충돌 없이 테스트
client = TestClient(app)

def test_health_returns_ok():
    response = client.get("/api/health")
    
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    