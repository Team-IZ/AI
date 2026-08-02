import os
import subprocess
import sys

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_health_is_exempt_from_auth():
    """헬스 체크는 키 없이도 통과해야 한다(운영 모니터링용)."""
    response = client.get("/api/health")

    assert response.status_code == 200


def test_production_without_key_fails_at_import():
    """production + 빈 키면 앱 import 자체가 실패해야 한다.

    지연 호출로 두면 기동은 성공하고 /api/health도 200이라 App Runner가
    배포를 정상으로 판정한 뒤 업무 요청만 전부 500이 된다(T9b에서 실제로 발생).
    별도 프로세스로 돌린다 — get_settings는 lru_cache라 같은 프로세스에서 못 다시 읽는다.
    """
    env = {**os.environ, "APP_ENV": "production", "INTERNAL_API_KEY": ""}
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        env=env, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "INTERNAL_API_KEY" in result.stderr