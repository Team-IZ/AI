""" 면담 브리프 생성(ib-1) 스키마·엔진·라우터 테스트.

명세: IZ-Get_면담브리프_생성API_명세서_v08.md. 부분 성공 없음(§5.2) -- 검증에 하나라도
걸리면 engine.generate()가 StageError를 올리고 라우터가 503+failureCode로 바꾼다.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.engines import interview_brief as engine
from app.engines.analysis import stages
from app.main import app
from app.schemas.interview_brief import InterviewBriefRequest

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}


def _request(**overrides) -> dict:
    """유효한 최소 요청. overrides로 특정 섹션만 바꿔 쓴다."""
    base = {
        "target": {
            "userName": "김OO", "className": "A반", "projectName": "미니프로젝트 3차",
            "projectCategory": "MINI_PROJECT", "roundName": "3회차",
        },
        "briefContext": {"briefType": "STANDARD", "isFirstInterview": False},
        "riskReasons": [{
            "reasonCode": "STAGE_DECLINE", "evaluationStatus": "MATCHED",
            "reasonSummary": "2회차 L3 도달 -> 3회차 L1 도달",
            "detectedAt": "2026-08-01T09:12:00Z",
            "sourceInterviewSourceId": "src-risk-1",
        }],
        "validityReview": {"status": "NOT_REQUIRED"},
        "comprehension": {
            "attemptType": "INITIAL", "attemptStatus": "COMPLETED",
            "terminalReasonCode": "COMPLETED", "sessionEndReasonCode": "TERMINATED_AT_L2",
            "problems": [{
                "problemNo": 1, "conceptName": "상태 관리",
                "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
                "stages": [{
                    "problemStageId": "ps-1", "axisCode": "L2", "status": "NOT_PASSED",
                    "questionText": "이 메서드가 호출되는 시점은?",
                    "questionAnswerText": "잘 모르겠습니다",
                    "questionScore": 1, "questionPassed": False,
                    "interviewSourceId": "src-stage-1",
                }],
            }],
        },
        "priorInterviews": [],
        "observationNotes": [],
    }
    base.update(overrides)
    return base


def _four_items(*, source_id: str = "src-stage-1") -> list[dict]:
    return [
        {"questionText": f"질문 {i}?", "questionRationale": "근거", "suggestedOrder": i,
         "interviewSourceId": source_id}
        for i in range(1, 5)
    ]


def _stub_call(monkeypatch, data: dict, usages: list[dict] | None = None):
    calls = []

    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        calls.append({"stage_id": stage_id, "values": values})
        return stages.StageResult(
            data=data,
            usages=usages if usages is not None else [{
                "model_code": model_code, "input_token_count": 500, "output_token_count": 100,
                "cached_token_count": 0, "status": "SUCCEEDED", "failure_code": None,
                "latency_ms": 1200,
            }],
        )

    monkeypatch.setattr(engine.stages, "call", _call)
    return calls


# ── 스키마 ────────────────────────────────────────────────────────────────

def test_request_validates_nested_structure():
    req = InterviewBriefRequest.model_validate(_request())
    assert req.target.user_name == "김OO"
    assert req.comprehension.problems[0].stages[0].interview_source_id == "src-stage-1"


def test_response_items_length_is_schema_enforced():
    from pydantic import ValidationError

    from app.schemas.interview_brief import InterviewBriefResponse

    with pytest.raises(ValidationError):
        InterviewBriefResponse.model_validate({
            "openingRemark": "안녕하세요",
            "items": _four_items()[:1],  # 1개, 스키마 하한(4) 위반
        })


# ── 엔진 ──────────────────────────────────────────────────────────────────

def test_generate_happy_path(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "지난달에 얘기 나눴었죠.", "items": _four_items()})

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert result.opening_remark == "지난달에 얘기 나눴었죠."
    assert len(result.items) == 4
    assert [i.suggested_order for i in result.items] == [1, 2, 3, 4]


def test_flagged_stage_excluded_from_prompt_and_allowed_ids(monkeypatch):
    """isFlagged=true 단계는 프롬프트 텍스트에도, 허용 interviewSourceId 집합에도 없어야 한다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["stages"].append({
        "problemStageId": "ps-2", "axisCode": "L3", "status": "NOT_REACHED",
        "questionText": "flagged 질문", "isFlagged": True, "interviewSourceId": "src-flagged",
    })
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    comprehension_block = calls[0]["values"]["comprehension_block"]
    assert "src-flagged" not in comprehension_block
    assert "flagged 질문" not in comprehension_block


def test_fabricated_interview_source_id_is_rejected(monkeypatch):
    _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _four_items(source_id="src-INVENTED"),
    })

    with pytest.raises(stages.StageError, match="지어냈습니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_too_few_items_is_rejected(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()[:1]})

    with pytest.raises(stages.StageError, match=r"개수가.*벗어났습니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_first_interview_requires_at_least_six_items(monkeypatch):
    """isFirstInterview=true면 4개로는 부족하다(6~8개 필요)."""
    req_dict = _request()
    req_dict["briefContext"]["isFirstInterview"] = True
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    with pytest.raises(stages.StageError, match=r"개수가.*벗어났습니다"):
        engine.generate(InterviewBriefRequest.model_validate(req_dict))


def test_non_contiguous_suggested_order_is_rejected(monkeypatch):
    items = _four_items()
    for i, item in enumerate(items):
        item["suggestedOrder"] = (i + 1) * 2  # 2, 4, 6, 8
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    with pytest.raises(stages.StageError, match="연속 정수가 아닙니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_not_generated_problem_has_no_stages_and_is_not_treated_as_failure(monkeypatch):
    """NOT_GENERATED 문제는 stages가 비어 있고, 미달이 아니라 '안 물어봤다'로 취급된다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0] = {
        "problemNo": 2, "conceptName": "동시성", "problemScope": "TEAM_SHARED_PROBLEM",
        "generationStatus": "NOT_GENERATED", "notGeneratedReasonCode": "NO_MATCHING_CODE_EVIDENCE",
        "stages": [],
    }
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items(source_id="src-risk-1")})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    comprehension_block = calls[0]["values"]["comprehension_block"]
    assert "출제되지 않았다" in comprehension_block
    assert "미달로 해석하지" in comprehension_block


def test_student_answer_text_is_fenced_as_untrusted(monkeypatch):
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    engine.generate(InterviewBriefRequest.model_validate(_request()))

    block = calls[0]["values"]["comprehension_block"]
    assert "answer_START" in block and "answer_END" in block
    assert "잘 모르겠습니다" in block  # 내용 자체는 그대로 전달(차단이 아니라 표시)


# ── 라우터 ────────────────────────────────────────────────────────────────

def test_router_returns_200_with_usage_headers(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["openingRemark"] == "여는 말"
    assert r.headers["x-ai-usage-status"] == "SUCCEEDED"
    assert r.headers["x-ai-usage-model-code"]


def test_router_usage_meta_body_field_matches_headers(monkeypatch):
    """D-ib2: 헤더/본문 둘 다 채우고, 같은 값이어야 한다(같은 dict에서 뽑으므로)."""
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    meta = r.json()["usageMeta"]
    assert meta["modelCode"] == r.headers["x-ai-usage-model-code"]
    assert str(meta["inputTokenCount"]) == r.headers["x-ai-usage-input-tokens"]
    assert str(meta["outputTokenCount"]) == r.headers["x-ai-usage-output-tokens"]
    assert str(meta["latencyMs"]) == r.headers["x-ai-usage-latency-ms"]
    assert meta["status"] == r.headers["x-ai-usage-status"] == "SUCCEEDED"
    assert meta["failureCode"] is None


def test_router_returns_503_with_failure_code_on_stage_error(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()[:1]})

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    assert r.status_code == 503
    assert r.json()["failureCode"] == "INVALID_JSON"
    assert "message" in r.json()


def test_router_maps_llm_transport_failure_code_through(monkeypatch):
    """LLM 전송 실패(예: RATE_LIMITED)는 검증 실패(INVALID_JSON)와 구분해 그대로 전달한다."""
    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        raise stages.StageError("ib-1: 실패", usages=[{
            "model_code": model_code, "input_token_count": 10, "output_token_count": 0,
            "cached_token_count": 0, "status": "FAILED", "failure_code": "RATE_LIMITED",
            "latency_ms": 300,
        }])

    monkeypatch.setattr(engine.stages, "call", _call)

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    assert r.status_code == 503
    assert r.json()["failureCode"] == "RATE_LIMITED"


def test_idempotency_key_reuse_skips_recomputation(monkeypatch):
    """같은 X-Idempotency-Key로 재전송하면 stages.call을 다시 부르지 않는다."""
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})
    headers = {**HEADERS, "X-Idempotency-Key": "interview-1:retry-test"}

    first = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=headers)
    second = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1  # 두 번째 요청은 캐시에서 그대로 반환됐다
