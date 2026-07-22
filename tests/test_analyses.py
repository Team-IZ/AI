from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)

# .env 키 설정돼 있으면 인증 켜짐. 설정에서 읽어 그대로 보내기
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

VALID_BODY = {
    "method": "GITHUB_URL",
    "source": {"repoUrl": "https://github.com/owner/repo"},
    "extractionScope": "TOTAL",
    "questionBudget": 4,
}


def test_accepts_valid_request():
    """정상 요청 → 202 + QUEUED. jobId가 camelCase로 나가는지도 함께 고정한다."""
    response = client.post("/api/v0/analyses", json=VALID_BODY, headers=HEADERS)
    
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["jobId"]  # camelCase로 나가는지 확인
    
def test_rejects_github_url_without_repo_url():
    """GITHUB_URL인데 repoUrl이 없으면 422. 계약 에러 형식({error, message, retryable})도 확인."""
    payload = {**VALID_BODY, "source": {}}
    
    response = client.post("/api/v0/analyses", json=payload, headers=HEADERS)
    
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "INVALID_REQUEST"
    assert body["retryable"] is False
    
def test_rejects_own_commit_without_commit_email():
    """OWN_COMMIT인데 commitEmail이 없으면 422. 위와 같은 조건부 필수 규칙의 다른 축."""
    payload = {**VALID_BODY, "extractionScope": "OWN_COMMIT"}
    
    response = client.post("/api/v0/analyses", json=payload, headers=HEADERS)
    
    assert response.status_code == 422
    
def test_same_idempotency_key_returns_same_job_id():
    """같은 멱등성 키 재요청 → 처음 만든 jobId를 그대로 반환(중복 job 생성 안 함)."""
    headers = {**HEADERS, "Idempotency-Key": "sub-1:1"}

    first = client.post("/api/v0/analyses", json=VALID_BODY, headers=headers)
    second = client.post("/api/v0/analyses", json=VALID_BODY, headers=headers)

    assert first.json()["jobId"] == second.json()["jobId"]    
    
def test_different_idempotency_key_returns_new_job_id():
    """키가 다르면 별개 요청 → 새 jobId. 재제출이 정상적으로 새 분석이 되는지."""
    a = client.post(
        "/api/v0/analyses", json=VALID_BODY, headers={**HEADERS, "Idempotency-Key": "sub-2:1"}
    )
    b = client.post(
        "/api/v0/analyses", json=VALID_BODY, headers={**HEADERS, "Idempotency-Key": "sub-3:1"}
    )

    assert a.json()["jobId"] != b.json()["jobId"]