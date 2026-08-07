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
                "conceptNameSource": "TEACHES_CANONICAL_NAME",
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
                # 실물 llm/client.py가 매 호출에 넣는 값. 빠뜨리면 AiUsage 검증에
                # 걸려 to_ai_usage가 그 행을 **조용히 버린다**(원장이 통째로 빈다).
                "occurred_at": "2026-08-07T09:00:00Z",
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


def test_evidenceless_item_is_dropped_when_there_are_no_observation_notes(monkeypatch):
    """🔴 백엔드 회신 §3 D-1②: observationNotes가 비면 근거 없는 항목을 아예 넣지 않는다.

    `interview_brief_item.interview_source_id`가 UUID NOT NULL이고 테이블 COMMENT가
    "매니저 수동 추가 항목을 지원하지 않는다"라, 근거 없는 항목은 **저장 경로가 없다.**
    프롬프트에도 같은 지시를 넣었지만 모델이 어길 수 있어 코드가 최종 방어다.
    """
    items = [
        {"questionText": f"질문 {i}?", "questionRationale": "근거", "suggestedOrder": i,
         "interviewSourceId": "src-stage-1"}
        for i in range(1, 7)               # 6개 -- 2개를 버려도 하한 4를 지킨다
    ]
    items[0]["interviewSourceId"] = None
    del items[1]["interviewSourceId"]      # 필드 자체를 생략한 경우도 같은 처리
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert len(result.items) == 4
    assert all(i.interview_source_id == "src-stage-1" for i in result.items)
    # 버린 자리를 메워 1..N이 다시 연속이어야 한다(백엔드가 display_order를 여기서 판다)
    assert [i.suggested_order for i in result.items] == [1, 2, 3, 4]


def test_dropping_evidenceless_items_below_the_minimum_fails_loudly(monkeypatch):
    """버린 결과가 하한(4개)을 못 채우면 조용히 짧은 브리프를 내보내지 않는다.

    부분 성공 없음(§5.2)과 같은 원칙 -- 3개짜리 체크리스트를 성공으로 돌려주면
    매니저가 그게 정상인 줄 안다.
    """
    items = _four_items()
    items[0]["interviewSourceId"] = None
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    with pytest.raises(stages.StageError, match=r"근거 없는 항목 1개 제외 후"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_evidenceless_item_falls_back_when_observation_notes_exist(monkeypatch):
    """관찰 메모가 있는데도 모델이 id를 안 달았으면 시도 단위 id로 떨어뜨린다.

    이때는 "근거가 아예 없는 요청"이 아니라 모델이 관찰 메모를 안 쓴 것뿐이라,
    항목을 버리는 대신 실재하는 id(attempt)를 붙여 살린다 -- 요청에 실제로 있는
    값이라 위조가 아니고 백엔드가 그 행을 저장할 수 있다.
    """
    req_dict = _request()
    req_dict["observationNotes"] = [{
        "occurredAt": "2026-08-01T09:00:00Z", "content": "메모",
        "interviewSourceId": "src-note-1", "visibility": "MANAGER_ONLY",
    }]
    items = _four_items()
    items[0]["interviewSourceId"] = None
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    assert len(result.items) == 4
    assert result.items[0].interview_source_id == "src-attempt-1"
    assert result.items[1].interview_source_id == "src-stage-1"


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
        # NOT_GENERATED는 title조차 CHECK로 NULL이라 개념명 출처가 UNAVAILABLE이다.
        "conceptNameSource": "UNAVAILABLE",
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
        "triggerReasonCode": "EXCESSIVE_CONNECTION_LOSS",
        "decisionReasonCode": "REVIEWED_VIOLATION_CONFIRMED",
        "decisionNote": "표절 확인됨",
    }
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["validity_review_block"]
    assert "EXCESSIVE_CONNECTION_LOSS" in block
    assert "REVIEWED_VIOLATION_CONFIRMED" in block
    assert "표절 확인됨" in block


def test_code_context_and_problem_interview_source_id_reach_prompt(monkeypatch):
    """codeContext(기존 미사용)와 문제 단위 interviewSourceId(신규)가 프롬프트에
    실리고, 후자가 허용 집합에도 들어가는지."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["codeContext"] = {
        "language": "python", "path": "app/handlers.py", "lineStart": 10, "lineEnd": 20,
        # A-2의 6필드. 원문 대신 좌표+키+해시만 온다.
        "snippetKey": "snip-1", "snippetHash": "sha256:abc",
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
        # NOT_GENERATED는 title조차 CHECK로 NULL이라 개념명 출처가 UNAVAILABLE이다.
        "conceptNameSource": "UNAVAILABLE",
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
    '지어냄'으로 걸리면 안 된다. 빠진 항목이 시도 단위로 떨어지는지도 같이 본다."""
    req_dict = _request()
    req_dict["observationNotes"] = [{
        "occurredAt": "2026-08-01T09:00:00Z",
        "content": "쉬는 시간에 페어 프로그래밍이 힘들다고 얘기함",
        "interviewSourceId": "src-note-1", "visibility": "MANAGER_ONLY",
    }]
    items = _four_items()
    items[0]["interviewSourceId"] = "src-note-1"  # 관찰 메모 근거 -- 이제 실제 id로 인용 가능
    items[1]["interviewSourceId"] = None           # priorInterviews만 근거면 시도 단위로
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    assert result.items[0].interview_source_id == "src-note-1"
    assert result.items[1].interview_source_id == "src-attempt-1"


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

BRIEF_PATH = "/api/v0/interview-brief:generate"
# 멱등키는 이 경로에서 **필수**다(contextId가 null이라 ai_usage.idempotency_key의
# 폴백이 여기까지 내려온다). 테스트마다 다른 값을 써야 캐시가 안 겹친다.
KEY_HEADERS = {**HEADERS, "Idempotency-Key": "interview-1:default"}


def test_router_returns_200_with_ai_usage_in_the_body(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    r = client.post(BRIEF_PATH, json=_request(),
                    headers={**KEY_HEADERS, "Idempotency-Key": "interview-1:200",
                             "X-Trace-Id": "trace-1"})

    assert r.status_code == 200
    body = r.json()
    assert body["openingRemark"] == "여는 말"
    assert len(body["aiUsage"]) == 1
    usage = body["aiUsage"][0]
    assert usage["featureCode"] == "INTERVIEW_BRIEF_GENERATION"
    assert usage["contextType"] == "INTERVIEW_BRIEF"
    # contextId는 null이다 -- AI가 brief_id를 받은 적이 없다(DB도 이 컬럼만 NULL 허용).
    assert usage["contextId"] is None
    assert usage["traceId"] == "trace-1"
    # request_id·idempotency_key는 NOT NULL이라 멱등키로 폴백해야 한다. 특히
    # idempotency_key는 전역 UNIQUE라 빈 문자열이면 두 번째 호출이 곧바로 깨진다.
    assert usage["requestId"] == "interview-1:200"
    assert usage["idempotencyKey"] == "interview-1:200:INTERVIEW_BRIEF:1"
    assert usage["status"] == "SUCCEEDED"
    assert usage["modelCode"]
    # 옛 D-ib2의 헤더 이중 전송은 폐기됐다.
    assert "x-ai-usage-status" not in r.headers


def test_router_requires_the_idempotency_key(monkeypatch):
    """멱등키가 없으면 ai_usage.idempotency_key가 ':INTERVIEW_BRIEF:1'이 되어
    두 번째 호출이 전역 UNIQUE에 걸린다 -- 그 전에 422로 막는다."""
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})

    r = client.post(BRIEF_PATH, json=_request(), headers=HEADERS)

    assert r.status_code == 422


def test_router_returns_503_with_failure_code_on_stage_error(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()[:1]})

    r = client.post(BRIEF_PATH, json=_request(),
                    headers={**KEY_HEADERS, "Idempotency-Key": "interview-1:503"})

    assert r.status_code == 503
    assert r.json()["failureCode"] == "INVALID_JSON"
    assert "message" in r.json()


def test_failure_envelope_carries_the_burned_usage(monkeypatch):
    """🔴 백엔드 회신 §3 A-5: 성공·실패 **모든** 봉투에 사용량을 싣는다.

    `ai_usage.latency_ms`가 NOT NULL이고 status에 FAILED가 있어서 실패 호출도 원장에
    남아야 한다. 실패 응답에 자리가 없으면 태운 토큰이 통째로 사라진다 -- 무료 티어
    529 실패율이 64%라 사라지는 양이 적지 않다.
    """
    # LLM 호출은 성공했고(토큰을 태웠고) 그 뒤 계약 검증에서 걸린 경우
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()[:1]})

    r = client.post(BRIEF_PATH, json=_request(),
                    headers={**KEY_HEADERS, "Idempotency-Key": "interview-1:usage-on-fail"})

    assert r.status_code == 503
    usage = r.json()["aiUsage"]
    assert len(usage) == 1
    assert usage[0]["featureCode"] == "INTERVIEW_BRIEF_GENERATION"
    assert usage[0]["inputTokenCount"] == 500
    assert usage[0]["idempotencyKey"] == "interview-1:usage-on-fail:INTERVIEW_BRIEF:1"


def test_failure_envelope_has_an_empty_usage_list_when_nothing_was_burned(monkeypatch):
    """LLM을 부르기도 전에 죽었으면 빈 배열이다 -- 없는 호출을 지어내지 않는다."""
    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        raise stages.StageError("ib-1: 프롬프트 조립 실패", usages=[])

    monkeypatch.setattr(engine.stages, "call", _call)

    r = client.post(BRIEF_PATH, json=_request(),
                    headers={**KEY_HEADERS, "Idempotency-Key": "interview-1:no-usage"})

    assert r.status_code == 503
    assert r.json()["aiUsage"] == []


def test_router_maps_llm_transport_failure_code_through(monkeypatch):
    """LLM 전송 실패(예: RATE_LIMITED)는 검증 실패(INVALID_JSON)와 구분해 그대로 전달한다."""
    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        raise stages.StageError("ib-1: 실패", usages=[{
            "model_code": model_code, "input_token_count": 10, "output_token_count": 0,
            "cached_token_count": 0, "status": "FAILED", "failure_code": "RATE_LIMITED",
            "latency_ms": 300,
        }])

    monkeypatch.setattr(engine.stages, "call", _call)

    r = client.post(BRIEF_PATH, json=_request(),
                    headers={**KEY_HEADERS, "Idempotency-Key": "interview-1:rate"})

    assert r.status_code == 503
    assert r.json()["failureCode"] == "RATE_LIMITED"


def test_idempotency_key_reuse_skips_recomputation(monkeypatch):
    """같은 멱등키 + 같은 본문 재전송이면 stages.call을 다시 부르지 않는다."""
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})
    headers = {**HEADERS, "Idempotency-Key": "interview-1:retry-test"}

    first = client.post(BRIEF_PATH, json=_request(), headers=headers)
    second = client.post(BRIEF_PATH, json=_request(), headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1  # 두 번째 요청은 캐시에서 그대로 반환됐다


def test_idempotency_key_reuse_with_a_different_body_is_a_conflict(monkeypatch):
    """🔴 키만 보고 캐시를 돌려주면 **다른 교육생의 브리프**가 나간다.

    develop이 같은 버그를 한 번 고쳤고(jobs.py, redteam audit H12), 백엔드 DDL도
    같은 판단을 했다(interview_brief.last_request_id + last_request_fingerprint 쌍).
    """
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _four_items()})
    headers = {**HEADERS, "Idempotency-Key": "interview-1:collision"}

    first = client.post(BRIEF_PATH, json=_request(), headers=headers)

    other = _request()
    other["target"]["userName"] = "다른 교육생"
    second = client.post(BRIEF_PATH, json=other, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"] == "IDEMPOTENCY_CONFLICT"
    assert len(calls) == 1  # 두 번째는 아예 생성하지 않았다
