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
            "attemptInterviewSourceId": "src-attempt-1",
            "sessionInterviewSourceId": "src-session-1",
            "problems": [{
                "problemNo": 1, "conceptName": "상태 관리",
                "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
                "interviewSourceId": "src-problem-1",
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


def test_null_interview_source_id_is_accepted_not_fabrication(monkeypatch):
    """D-ib3(실LLM 호출로 발견): priorInterviews/observationNotes/briefContext는
    명세상 id가 없다. 그 근거만으로 만든 라포 질문이 interviewSourceId를 null로
    두는 건 정직한 미기재이지 위조가 아니다 -- 빈 문자열/None 둘 다 허용해야 한다."""
    items = _four_items()
    items[0]["interviewSourceId"] = None
    del items[1]["interviewSourceId"]  # 아예 필드 자체를 생략한 경우도 허용
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert result.items[0].interview_source_id is None
    assert result.items[1].interview_source_id is None
    assert result.items[2].interview_source_id == "src-stage-1"  # 나머지는 정상 그대로


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
        "interviewSourceId": "src-problem-2", "stages": [],
    }
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items(source_id="src-risk-1")})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    comprehension_block = calls[0]["values"]["comprehension_block"]
    assert "출제되지 않았다" in comprehension_block
    assert "미달로 해석하지" in comprehension_block


# ── D-ib4 (백엔드 감사 반영: 미사용 필드 배선 + 신규 interviewSourceId 슬롯) ──────

def test_risk_reason_stage_link_and_not_applicable_code_reach_prompt(monkeypatch):
    """sourceProblemStageId/notApplicableReasonCode는 스키마엔 있었지만 프롬프트에
    안 실리고 있었다(백엔드 감사로 발견) -- 배선 확인."""
    req_dict = _request()
    req_dict["riskReasons"] = [{
        "reasonCode": "CONTRIBUTION_UNDERSTANDING_GAP", "evaluationStatus": "NOT_APPLICABLE",
        "notApplicableReasonCode": "FIRST_MINI_PROJECT",
        "reasonSummary": "첫 미니프로젝트라 비교 대상 없음",
        "detectedAt": "2026-08-01T09:12:00Z",
        "sourceProblemStageId": "ps-linked-1",
        "sourceInterviewSourceId": "src-risk-1",
    }]
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["risk_reasons_block"]
    assert "ps-linked-1" in block
    assert "FIRST_MINI_PROJECT" in block


def test_validity_trigger_and_decision_reason_code_reach_prompt(monkeypatch):
    """trigger_reason_code(신규)·decision_reason_code(기존 미사용)가 프롬프트에 실리는지."""
    req_dict = _request()
    req_dict["validityReview"] = {
        "status": "CONFIRMED_INVALID",
        "triggerReasonCode": "DUPLICATE_SUBMISSION_DETECTED",
        "decisionReasonCode": "PLAGIARISM_CONFIRMED",
        "decisionNote": "표절 확인됨",
    }
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["validity_review_block"]
    assert "DUPLICATE_SUBMISSION_DETECTED" in block
    assert "PLAGIARISM_CONFIRMED" in block
    assert "표절 확인됨" in block


def test_code_context_and_problem_interview_source_id_reach_prompt(monkeypatch):
    """codeContext(기존 미사용)와 문제 단위 interviewSourceId(신규)가 프롬프트에
    실리고, 후자가 허용 집합에도 들어가는지."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["codeContext"] = {
        "language": "python", "path": "app/handlers.py", "lineStart": 10, "lineEnd": 20,
    }
    calls = _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _four_items(source_id="src-problem-1"),  # 문제 단위 id로 응답
    })

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["comprehension_block"]
    assert "app/handlers.py:10-20" in block
    assert "src-problem-1" in block
    assert result.items[0].interview_source_id == "src-problem-1"  # 지어냄으로 안 걸림


def test_not_generated_problem_interview_source_id_is_allowed_even_with_empty_stages(monkeypatch):
    """NOT_GENERATED 문제(stages=[])도 problem 단위 id는 허용 집합에 들어가야 한다
    -- _collect_allowed_source_ids()가 stages 루프 밖에서 넣는지 확인(누락하기 쉬운 지점)."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0] = {
        "problemNo": 2, "conceptName": "동시성", "problemScope": "TEAM_SHARED_PROBLEM",
        "generationStatus": "NOT_GENERATED", "notGeneratedReasonCode": "NO_MATCHING_CODE_EVIDENCE",
        "interviewSourceId": "src-problem-2", "stages": [],
    }
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items(source_id="src-problem-2")})

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    assert result.items[0].interview_source_id == "src-problem-2"


def test_attempt_and_session_interview_source_id_are_allowed(monkeypatch):
    """시도/세션 단위 interviewSourceId(신규, NOT_ATTENDED 등 problems=[] 케이스의
    유일한 근거)가 프롬프트에 실리고 허용 집합에도 들어가는지."""
    req_dict = _request()
    req_dict["comprehension"] = {
        "attemptType": "INITIAL", "attemptStatus": "FAILED",
        "terminalReasonCode": "NOT_ATTENDED",
        "attemptInterviewSourceId": "src-attempt-9",
        "problems": [],
    }
    calls = _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _four_items(source_id="src-attempt-9"),
    })

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["comprehension_block"]
    assert "src-attempt-9" in block
    assert result.items[0].interview_source_id == "src-attempt-9"


def test_observation_note_interview_source_id_is_allowed_not_fabrication(monkeypatch):
    """관찰 메모가 이제 자기 interviewSourceId를 갖는다(D-ib4) -- 그 값으로 응답해도
    '지어냄'으로 걸리면 안 된다. D-ib3의 null-허용 규칙이 여전히 유효한지도 같이 본다
    (priorInterviews만 근거인 항목은 계속 null 허용)."""
    req_dict = _request()
    req_dict["observationNotes"] = [{
        "occurredAt": "2026-08-01T09:00:00Z",
        "content": "쉬는 시간에 페어 프로그래밍이 힘들다고 얘기함",
        "interviewSourceId": "src-note-1",
    }]
    items = _four_items()
    items[0]["interviewSourceId"] = "src-note-1"  # 관찰 메모 근거 -- 이제 실제 id로 인용 가능
    items[1]["interviewSourceId"] = None           # priorInterviews만 근거면 여전히 null 허용
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    assert result.items[0].interview_source_id == "src-note-1"
    assert result.items[1].interview_source_id is None


def test_new_session_end_reason_codes_are_accepted(monkeypatch):
    """백엔드 DDL 실측(ck_assessment_session_end_reason_code)으로 발견된 4종 --
    기존 스키마는 9종뿐이라 이 값들이 오면 422였다."""
    for code in ("REVIEW_DUE_AT_EXPIRED", "DATA_INTEGRITY_INVALID",
                 "ADMIN_INVALIDATED", "TECHNICAL_FAILURE"):
        req_dict = _request()
        req_dict["comprehension"]["sessionEndReasonCode"] = code
        req = InterviewBriefRequest.model_validate(req_dict)  # 422 없이 통과해야 함
        assert req.comprehension.session_end_reason_code == code


def test_concept_name_source_hedges_language_when_not_verified(monkeypatch):
    """conceptNameSource(D-2 대응)가 VERIFICATION_CONCEPT가 아니면 확신도를 낮추라는
    지시가 프롬프트에 붙어야 한다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["conceptNameSource"] = "PROBLEM_TITLE"
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["comprehension_block"]
    assert "단정하지 말고" in block


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
