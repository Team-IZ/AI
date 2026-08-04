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
    # D-fix (redteam audit H12, 2026-08-04): job_id_for_key()가 이제 재사용 요청의
    # submissionId/attemptId를 최초 요청과 대조한다(둘 다 없으면 거부) -- 실제 Spring
    # 호출은 이 값들을 항상 보낸다는 전제이므로, 그걸 안 흉내내던 이 fixture가 현실과
    # 어긋나 있었다. 값 자체는 임의값이고, 같은 dict를 재사용하는 테스트는 두 호출 다
    # 같은 값이라 여전히 일치한다.
    "submissionId": "sub-1",
    "attemptId": "1",
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
    
def test_reused_idempotency_key_with_mismatched_identity_is_rejected():
    """멱등키 추측/재사용 -- submissionId/attemptId가 최초 요청과 다르면 409 (H12)."""
    headers = {**HEADERS, "Idempotency-Key": "sub-guessed:1"}
    client.post("/api/v0/analyses", json=VALID_BODY, headers=headers)

    guess = {**VALID_BODY, "submissionId": "someone-elses-sub", "attemptId": "9"}
    response = client.post("/api/v0/analyses", json=guess, headers=headers)

    assert response.status_code == 409
    assert response.json()["error"] == "IDEMPOTENCY_CONFLICT"


def test_reused_idempotency_key_without_any_identity_is_rejected():
    """submissionId/attemptId를 둘 다 생략한 재사용도 거부된다 --
    None==None으로 통과하던 구멍(H12)."""
    headers = {**HEADERS, "Idempotency-Key": "sub-noident:1"}
    client.post("/api/v0/analyses", json=VALID_BODY, headers=headers)

    no_identity = {k: v for k, v in VALID_BODY.items() if k not in ("submissionId", "attemptId")}
    response = client.post("/api/v0/analyses", json=no_identity, headers=headers)

    assert response.status_code == 409
    assert response.json()["error"] == "IDEMPOTENCY_CONFLICT"


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
    
def test_stage_rejects_broken_hint_pair():
    """힌트 2개·[1,2] 순서가 아니면 스키마에서 막는다. 동결이라 런타임 보정이 없다."""
    import pytest
    from pydantic import ValidationError

    from app.schemas.analysis import ProblemStage

    base = {"axisCode": "L1", "questionText": "무엇을 하는 코드인가"}

    with pytest.raises(ValidationError):   # 1개뿐
        ProblemStage.model_validate({**base, "hints": [{"hintLevel": 1, "hintText": "a"}]})

    with pytest.raises(ValidationError):   # 순서 뒤집힘
        ProblemStage.model_validate({
            **base,
            "hints": [{"hintLevel": 2, "hintText": "b"}, {"hintLevel": 1, "hintText": "a"}],
        })
        
def test_problem_rejects_wrong_stage_order():
    """단계는 L1→L4 정확히 4개. 순서가 어긋나면 루브릭이 틀린 축에 붙는다."""
    import pytest
    from pydantic import ValidationError

    from app.schemas.analysis import Problem

    def stage(axis: str) -> dict:
        # 전면 동결 — 4축 전부 분석 때 질문 1개와 힌트 2개가 채워진다.
        return {
            "axisCode": axis,
            "questionText": f"{axis} 질문",
            "hints": [{"hintLevel": 1, "hintText": "h1"}, {"hintLevel": 2, "hintText": "h2"}],
        }

    base = {
        "problemId": "p-1", "problemNo": 1, "problemType": "RISK_POINT",
        "priority": 0.9, "sourcePath": "app/main.py",
        "lineStart": 10, "lineEnd": 20,
        "codeSnippet": "x = 1", "evidenceHash": "a" * 64,
        "extractorVersion": 1,
    }

    with pytest.raises(ValidationError):   # L3·L4 뒤집힘
        Problem.model_validate({**base, "stages": [stage(a) for a in ("L1", "L2", "L4", "L3")]})

    with pytest.raises(ValidationError):   # 3개뿐
        Problem.model_validate({**base, "stages": [stage(a) for a in ("L1", "L2", "L3")]})

    ok = Problem.model_validate({**base, "stages": [stage(a) for a in ("L1", "L2", "L3", "L4")]})
    assert ok.status == "READY"


def test_extractor_version_must_be_a_positive_integer():
    """DB assessment_problem.extractor_version은 INTEGER CHECK (> 0)다.

    문자열("v0")로 보내던 시절이 있었고, 그대로 나가면 Spring INSERT가 깨진다.
    응답 검증에서 안 막으면 분석은 SUCCEEDED로 끝나고 저장에서 터진다.
    """
    import pytest
    from pydantic import ValidationError

    from app.schemas.analysis import Problem

    def stage(axis: str) -> dict:
        return {
            "axisCode": axis, "questionText": f"{axis} 질문",
            "hints": [{"hintLevel": 1, "hintText": "h1"}, {"hintLevel": 2, "hintText": "h2"}],
        }

    base = {
        "problemId": "p-1", "problemNo": 1, "problemType": "RISK_POINT",
        "priority": 0.9, "sourcePath": "app/main.py", "lineStart": 10, "lineEnd": 20,
        "codeSnippet": "x = 1", "evidenceHash": "a" * 64,
        "stages": [stage(a) for a in ("L1", "L2", "L3", "L4")],
    }

    with pytest.raises(ValidationError):       # 룰 버전 문자열
        Problem.model_validate({**base, "extractorVersion": "rules-a1b2c3"})

    with pytest.raises(ValidationError):       # CHECK (> 0)
        Problem.model_validate({**base, "extractorVersion": 0})

    assert Problem.model_validate({**base, "extractorVersion": 7}).extractor_version == 7


def test_teach_id_is_echoed_on_problems():
    """문제↔개념 연결이 응답에 있어야 '클래스는 L3까지' 화면을 만들 수 있다."""
    payload = {**VALID_BODY,
               "teaches": [{"id": "tch-1", "label": "클래스"},
                           {"id": "tch-2", "label": "상속"}]}
    post = client.post("/api/v0/analyses", json=payload, headers=HEADERS)

    problems = client.get(f"/api/v0/analyses/{post.json()['jobId']}",
                          headers=HEADERS).json()["result"]["problems"]

    assert [p["teachId"] for p in problems] == ["tch-1", "tch-2", None]
    # teach 앵커가 없는 문제는 화면에 "일반 문제"로 표기된다. 둘은 짝이다.

def test_requirement_result_count_mismatch_fails_job():
    """판정이 빠진 채 SUCCEEDED가 되면 미판정 요구사항이 통과로 기록된다."""
    from app.engines import get_analysis_engine
    from app.engines.stub import StubAnalysisEngine
    from app.main import app

    class LazyEngine:
        def analyze(self, request, zip_bytes=None):
            raw = StubAnalysisEngine().analyze(request, zip_bytes)
            raw["requirement_results"] = []      # 판정을 통째로 빠뜨린다
            return raw

    app.dependency_overrides[get_analysis_engine] = lambda: LazyEngine()
    try:
        payload = {**VALID_BODY, "requirements": [{"requirementId": "req-1", "text": "t"}]}
        post = client.post("/api/v0/analyses", json=payload, headers=HEADERS)
        body = client.get(f"/api/v0/analyses/{post.json()['jobId']}", headers=HEADERS).json()

        assert body["status"] == "FAILED"
        assert body["result"] is None          # 검증 실패면 결과를 남기지 않는다
    finally:
        app.dependency_overrides.clear()

def test_every_axis_needs_question_and_two_hints():
    """전면 동결 — 4축 전부 분석 때 질문 1개 + 힌트 2개가 채워져야 한다.

    "0개 또는 2개"로 느슨하게 두면 힌트가 없어도 통과하고, 학생이 힌트 없이
    재답변하게 된다 — 에러 없이 동작만 틀린다.
    """
    import pytest
    from pydantic import ValidationError

    from app.schemas.analysis import ProblemStage

    two_hints = [{"hintLevel": 1, "hintText": "h1"}, {"hintLevel": 2, "hintText": "h2"}]

    # 정상 — 4축 전부 같은 규칙
    for axis in ("L1", "L2", "L3", "L4"):
        ProblemStage.model_validate({"axisCode": axis, "questionText": "q", "hints": two_hints})

    with pytest.raises(ValidationError):   # 질문이 없다
        ProblemStage.model_validate({"axisCode": "L1", "questionText": None, "hints": two_hints})

    with pytest.raises(ValidationError):   # 힌트가 없다
        ProblemStage.model_validate({"axisCode": "L2", "questionText": "q", "hints": []})

    with pytest.raises(ValidationError):   # L4도 예외가 아니다 — 옛 혼합 모드에선 통과했다
        ProblemStage.model_validate({"axisCode": "L4", "questionText": None, "hints": []})

    with pytest.raises(ValidationError):   # 힌트 순서가 뒤집혔다
        ProblemStage.model_validate({
            "axisCode": "L3", "questionText": "q",
            "hints": [{"hintLevel": 2, "hintText": "h2"}, {"hintLevel": 1, "hintText": "h1"}],
        })
