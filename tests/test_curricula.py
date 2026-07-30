""" 교안 분석 엔드포인트 스텁 테스트. 분석·보고서와 같은 비동기 job 패턴. """
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import get_settings
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

PAYLOAD = {"versionId": "ver-1", "modelCode": "stub-0"}
PDF = ("teach.pdf", b"%PDF-1.4 stub", "application/pdf")


def _post(headers: dict | None = None) -> dict:
    r = client.post(
        "/api/v0/curricula",
        data={"payload": json.dumps(PAYLOAD)},
        files={"file": PDF},
        headers={**HEADERS, **(headers or {})},
    )
    return r.json() | {"_status": r.status_code}


def _result() -> dict:
    job_id = _post()["jobId"]
    return client.get(f"/api/v0/curricula/{job_id}", headers=HEADERS).json()


def test_accepts_curriculum_request():
    """정상 요청 → 202 + QUEUED, camelCase jobId."""
    body = _post()

    assert body["_status"] == 202
    assert body["status"] == "QUEUED"
    assert body["jobId"]


def test_rejects_non_pdf():
    """PDF가 아니면 422. 잘못된 파일로 1~2분짜리 LLM 작업을 돌리지 않는다."""
    r = client.post(
        "/api/v0/curricula",
        data={"payload": json.dumps(PAYLOAD)},
        files={"file": ("notes.csv", b"a,b,c", "text/csv")},
        headers=HEADERS,
    )

    assert r.status_code == 422
    assert r.json()["error"] == "INVALID_REQUEST"


def test_rejects_malformed_payload():
    """payload가 JSON이 아니면 422."""
    r = client.post(
        "/api/v0/curricula",
        data={"payload": "{not json"},
        files={"file": PDF},
        headers=HEADERS,
    )

    assert r.status_code == 422


def test_same_idempotency_key_returns_same_job_id():
    """같은 멱등키 재요청 → 처음 jobId 그대로. 같은 PDF를 두 번 돌리지 않는다."""
    first = _post({"Idempotency-Key": "ver-1:1"})
    second = _post({"Idempotency-Key": "ver-1:1"})

    assert first["jobId"] == second["jobId"]


def test_result_has_three_levels():
    """analysis → section → teaches 3계층이 그대로 나와야 Spring이 INSERT할 수 있다."""
    body = _result()

    assert body["status"] == "SUCCEEDED"
    result = body["result"]
    assert result["versionId"] == "ver-1"

    section = result["sections"][0]
    assert section["moduleNo"] == 1
    assert section["pageEnd"] >= section["pageStart"]
    assert section["teaches"][0]["canonicalName"]
    assert section["teaches"][0]["normalizedName"]


def test_teach_without_description_is_allowed():
    """개념만 등장하고 설명이 없는 경우가 실제로 흔하다. NULL 경로가 살아 있어야 한다."""
    teaches = _result()["result"]["sections"][0]["teaches"]
    bare = [t for t in teaches if t["canonicalDescription"] is None]

    assert bare, "설명 없는 개념이 스텁에 하나는 있어야 백엔드가 NULL 경로를 본다"
    assert bare[0]["descriptionPageStart"] is None
    assert bare[0]["descriptionPageEnd"] is None


def test_section_rejects_reversed_pages():
    """DB CHECK(page_end >= page_start)를 스키마에서 먼저 막는다."""
    from app.schemas.curriculum import CurriculumSection

    with pytest.raises(ValidationError):
        CurriculumSection.model_validate(
            {"moduleNo": 1, "title": "t", "pageStart": 10, "pageEnd": 3, "teaches": []}
        )


def test_unknown_curriculum_job_returns_404():
    """모르는 jobId → 404 JOB_NOT_FOUND, retryable=false."""
    r = client.get("/api/v0/curricula/nope", headers=HEADERS)

    assert r.status_code == 404
    assert r.json()["error"] == "JOB_NOT_FOUND"
    assert r.json()["retryable"] is False