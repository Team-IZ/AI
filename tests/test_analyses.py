import io
import json
import zipfile

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
    
def _zip_bytes() -> bytes:
    """테스트용 최소 ZIP. 내용은 안 쓰이지만 형식은 진짜여야 한다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("main.py", "print('hello')")
    return buffer.getvalue()


def test_accepts_zip_upload():
    """multipart로 payload+file을 보내면 202. JSON 경로와 같은 스키마를 쓴다."""
    payload = {"method": "ZIP_WITH_GITLOG", "extractionScope": "TOTAL"}

    response = client.post(
        "/api/v0/analyses",
        data={"payload": json.dumps(payload)},
        files={"file": ("submission.zip", _zip_bytes(), "application/zip")},
        headers=HEADERS,
    )

    assert response.status_code == 202
    assert response.json()["jobId"]


def test_rejects_zip_method_without_file():
    """method=ZIP_WITH_GITLOG인데 JSON으로만 보내면 422. Content-Type과 method 불일치."""
    payload = {"method": "ZIP_WITH_GITLOG", "extractionScope": "TOTAL"}

    response = client.post("/api/v0/analyses", json=payload, headers=HEADERS)

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_REQUEST"


def test_rejects_multipart_without_payload():
    """multipart인데 payload 폼 필드가 없으면 422."""
    response = client.post(
        "/api/v0/analyses",
        files={"file": ("submission.zip", _zip_bytes(), "application/zip")},
        headers=HEADERS,
    )

    assert response.status_code == 422


def test_rejects_malformed_json_body():
    """본문이 JSON이 아니면 422. 필드 경로가 아니라 파싱 실패 메시지가 나가야 한다."""
    response = client.post(
        "/api/v0/analyses",
        content="not json",
        headers={**HEADERS, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_REQUEST"


def test_openapi_documents_both_content_types():
    """Swagger에 JSON·multipart 두 형태가 노출되고, JSON 스키마가 실체를 갖는지 고정한다.

    자동 바인딩을 포기하면 $ref가 빈 곳을 가리키기 쉬운 자리다.
    """
    schema = client.get("/openapi.json").json()
    content = schema["paths"]["/api/v0/analyses"]["post"]["requestBody"]["content"]

    assert "application/json" in content
    assert "multipart/form-data" in content

    json_schema = content["application/json"]["schema"]
    # $ref만 남아 있으면 실체가 없다는 뜻 — 필드가 실제로 실려야 한다
    assert "properties" in json_schema
    assert "method" in json_schema["properties"]
    assert "questionBudget" in json_schema["properties"]
    
def _create_job() -> str:
    """POST로 job 하나 만들고 jobId를 돌려주는 도우미."""
    response = client.post("/api/v0/analyses", json=VALID_BODY, headers=HEADERS)
    return response.json()["jobId"]


def test_returns_job_status_and_result():
    """POST로 만든 job을 GET으로 조회 → 상태와 결과가 나온다."""
    job_id = _create_job()

    response = client.get(f"/api/v0/analyses/{job_id}", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["jobId"] == job_id
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["snapshotId"]
    assert body["result"]["questionCountPlanned"] >= 0


def test_status_uses_db_allowed_values():
    """status는 analysis_job.status의 CHECK 제약 안에 있어야 한다.

    ANALYZING·READY는 다른 테이블 값이다. 쓰면 Spring INSERT가 깨진다.
    """
    job_id = _create_job()

    body = client.get(f"/api/v0/analyses/{job_id}", headers=HEADERS).json()

    assert body["status"] in {"QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"}


def test_ai_usage_is_empty_for_rule_based_analysis():
    """P02는 LLM을 쓰지 않으므로 aiUsage는 항상 빈 배열이다."""
    job_id = _create_job()

    body = client.get(f"/api/v0/analyses/{job_id}", headers=HEADERS).json()

    assert body["aiUsage"] == []


def test_unknown_job_id_returns_404():
    """모르는 jobId → 404 JOB_NOT_FOUND. 재시도해도 소용없으니 retryable=false."""
    response = client.get("/api/v0/analyses/does-not-exist", headers=HEADERS)

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "JOB_NOT_FOUND"
    assert body["retryable"] is False