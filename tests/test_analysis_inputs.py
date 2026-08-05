""" POST /analysis-inputs -- 스키마 검증, failureCode 응답 모양, analysisInputId 결정성.

실제 fetch(git clone 등)는 `fetch_engine.fetch`를 가짜로 바꿔 네트워크를 안 탄다 --
그건 `tests/test_fetch.py`와 이번 세션의 수동 실네트워크 확인(octocat/Hello-World)에서
이미 검증됨. 여기는 라우트 계층(검증→호출→응답 조립)만 확인한다.
"""
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.config import get_settings
from app.engines.analysis import fetch as fetch_engine
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

VALID_GITHUB_BODY = {
    "requestId": "11111111-1111-1111-1111-111111111111",
    "method": "GITHUB_URL",
    "orgId": "org-1",
    "repositoryUrl": "https://github.com/owner/repo",
}


def _patch_fetch(monkeypatch, fetched_input):
    """`fetch_engine.fetch(spec)`를 항상 `fetched_input`을 내는 가짜 컨텍스트매니저로 바꾼다."""
    @contextmanager
    def _cm():
        yield fetched_input

    monkeypatch.setattr(fetch_engine, "fetch", lambda spec: _cm())


def _sample_result(**overrides) -> fetch_engine.FetchedInput:
    base = dict(
        root="/tmp/unused", method="GITHUB_URL", resolved_branch="main",
        head_commit={"sha": "a" * 40, "message": "m", "committed_at": "2026-01-01T00:00:00+00:00"},
        git_history=[], git_history_source="NONE", history_truncated=False,
        input_hash="0" * 64, file_count=3, byte_count=100,
    )
    base.update(overrides)
    return fetch_engine.FetchedInput(**base)


# ── 스키마 검증 -- fetch까지 안 감 ──────────────────────────────────────────


def test_rejects_github_url_without_repository_url():
    payload = {**VALID_GITHUB_BODY, "repositoryUrl": None}
    response = client.post("/api/v0/analysis-inputs", json=payload, headers=HEADERS)
    assert response.status_code == 422


def test_rejects_zip_without_download_url():
    payload = {
        "requestId": "22222222-2222-2222-2222-222222222222",
        "method": "ZIP_WITH_GITLOG", "orgId": "org-1",
    }
    response = client.post("/api/v0/analysis-inputs", json=payload, headers=HEADERS)
    assert response.status_code == 422


def test_rejects_unknown_method():
    payload = {**VALID_GITHUB_BODY, "method": "SOMETHING_ELSE"}
    response = client.post("/api/v0/analysis-inputs", json=payload, headers=HEADERS)
    assert response.status_code == 422


def test_422_body_shape_is_failure_code_not_the_common_error_shape():
    """이 엔드포인트만 {failureCode,message,requestId}다 -- 다른 넷이 쓰는

    공용 {error,message,retryable}이 아니다(백엔드 프로포절이 이 모양을 명시)."""
    payload = {**VALID_GITHUB_BODY, "repositoryUrl": None}
    response = client.post("/api/v0/analysis-inputs", json=payload, headers=HEADERS)

    body = response.json()
    assert "failureCode" in body
    assert "error" not in body
    assert "retryable" not in body
    assert body["requestId"] == VALID_GITHUB_BODY["requestId"]


# ── 성공 경로(fetch 가짜로 대체) ───────────────────────────────────────────


def test_successful_github_fetch_returns_200_with_expected_shape(monkeypatch):
    _patch_fetch(monkeypatch, _sample_result())

    response = client.post("/api/v0/analysis-inputs", json=VALID_GITHUB_BODY, headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "GITHUB_URL"
    assert body["resolvedBranch"] == "main"
    assert body["headCommit"]["sha"] == "a" * 40
    assert body["inputHash"] == "0" * 64
    assert body["analysisInputId"]
    assert body["gitHistorySource"] == "NONE"


def test_analysis_input_id_is_deterministic_across_different_request_ids(monkeypatch):
    """D2 -- analysisInputId는 requestId가 아니라 (org, method, source, pin)로만

    정해진다. 같은 레포/커밋이면 requestId가 달라도(예: 백엔드 재시도) 같은 id가
    나와야 재fetch 시 그대로 참조할 수 있다."""
    _patch_fetch(monkeypatch, _sample_result())

    first = client.post("/api/v0/analysis-inputs", json=VALID_GITHUB_BODY, headers=HEADERS)
    second_body = {**VALID_GITHUB_BODY, "requestId": "99999999-9999-9999-9999-999999999999"}
    second = client.post("/api/v0/analysis-inputs", json=second_body, headers=HEADERS)

    assert first.json()["analysisInputId"] == second.json()["analysisInputId"]


def test_analysis_input_id_changes_for_a_different_repository(monkeypatch):
    _patch_fetch(monkeypatch, _sample_result())
    first = client.post("/api/v0/analysis-inputs", json=VALID_GITHUB_BODY, headers=HEADERS)

    other_body = {**VALID_GITHUB_BODY, "repositoryUrl": "https://github.com/other/repo"}
    second = client.post("/api/v0/analysis-inputs", json=other_body, headers=HEADERS)

    assert first.json()["analysisInputId"] != second.json()["analysisInputId"]


def test_git_history_source_reports_backend_supplied(monkeypatch):
    """D3 우선순위 ① -- 요청에 gitHistory가 실려 있으면 응답도 BACKEND_SUPPLIED로 보고한다."""
    supplied = [{
        "sha": "b" * 40, "author_name": "Alice", "author_email": "a@x.com",
        "committed_at": "2026-01-01T00:00:00+00:00", "changed_files": ["f.py"],
        "additions": 1, "deletions": 0,
    }]
    _patch_fetch(monkeypatch, _sample_result(
        git_history=supplied, git_history_source="BACKEND_SUPPLIED",
    ))

    payload = {**VALID_GITHUB_BODY, "gitHistory": [{
        "sha": "b" * 40, "authorName": "Alice", "authorEmail": "a@x.com",
        "committedAt": "2026-01-01T00:00:00+00:00", "changedFiles": ["f.py"],
        "additions": 1, "deletions": 0,
    }]}
    response = client.post("/api/v0/analysis-inputs", json=payload, headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["gitHistorySource"] == "BACKEND_SUPPLIED"
    assert body["gitHistory"][0]["authorEmail"] == "a@x.com"


def test_fetch_error_maps_to_its_failure_code(monkeypatch):
    """`fetch.FetchError`가 그대로 422 failureCode로 나가는지 -- 라우트가 코드를 뭉개지 않는지."""
    def _raise(spec):
        raise fetch_engine.FetchError("REPO_NOT_FOUND", "레포를 못 찾았습니다")

    monkeypatch.setattr(fetch_engine, "fetch", _raise)

    response = client.post("/api/v0/analysis-inputs", json=VALID_GITHUB_BODY, headers=HEADERS)

    assert response.status_code == 422
    body = response.json()
    assert body["failureCode"] == "REPO_NOT_FOUND"
    assert body["requestId"] == VALID_GITHUB_BODY["requestId"]
