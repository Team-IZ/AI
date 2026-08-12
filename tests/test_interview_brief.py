""" 면담 브리프 생성(ib-1) 스키마·엔진·라우터 테스트.

명세: IZ-Get_면담브리프_생성API_명세서_v08.md. 부분 성공 없음(§5.2) -- 검증에 하나라도
걸리면 engine.generate()가 StageError를 올리고 라우터가 503+failureCode로 바꾼다.

2026-08-12: 질문 개수 규칙이 "4~8개(첫 면담이면 6~8개)"에서 5-카테고리 고정 구성(라포1 +
이전면담0~1 + 위험0~1 + 일반2 + 문답N, N은 8개 상한에 맞춰 절삭)으로 바뀌었다. `_request()`
기본 픽스처(위험 사유 1개, priorInterviews=[], L2/NOT_PASSED 단계 1개)의 기대 구성은
RAPPORT, RISK, GENERAL, GENERAL, QNA 5개 -- `_matching_items()`가 그 구성에 맞는 items를
만들어준다.
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


def _matching_items(
    *, source_id: str | None = "src-stage-1", prior_interview: bool = False,
    risk: bool = True, qna_count: int = 1,
) -> list[dict]:
    """요청 픽스처의 실제 구성(라포1 + 이전면담0/1 + 위험0/1 + 일반2 + 문답N)과 정확히
    맞아떨어지는 items를 만든다. 기본값(prior_interview=False, risk=True, qna_count=1)은
    `_request()`의 기본 구성(RAPPORT, RISK, GENERAL, GENERAL, QNA 1개)과 같다 -- 다른 구성의
    픽스처를 쓰는 테스트는 그 구성에 맞게 인자를 바꾼다. interviewSourceId는 (원래
    `_four_items()`가 그랬듯) 전 항목에 같은 값을 싣는다 -- engine은 카테고리와 id의 의미적
    일치를 검증하지 않고 허용 집합 소속 여부만 본다."""
    types = ["RAPPORT"]
    if prior_interview:
        types.append("PRIOR_INTERVIEW")
    if risk:
        types.append("RISK")
    types += ["GENERAL", "GENERAL"]
    types += ["QNA"] * qna_count
    return [
        {
            "questionText": f"질문 {i}?", "questionRationale": f"근거{i}",
            "suggestedOrder": i, "questionType": t, "interviewSourceId": source_id,
        }
        for i, t in enumerate(types, start=1)
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
            "items": _matching_items()[:2],  # 2개, 스키마 하한(3) 위반
        })


# ── 질문 구성(2026-08-12: 5-카테고리 고정 순서) ──────────────────────────────

def test_qna_count_matches_l2_not_passed_stages():
    """L2 NOT_PASSED만 문답 관련 질문 근거로 센다 -- L1/L3 NOT_PASSED나 L2 PASSED는 안 센다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"] = [
        {
            "problemNo": 1, "conceptName": "상태 관리", "conceptNameSource": "TEACHES_CANONICAL_NAME",
            "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
            "interviewSourceId": "src-problem-1",
            "stages": [
                {"problemStageId": "ps-1a", "axisCode": "L1", "status": "NOT_PASSED",
                 "questionText": "q1", "interviewSourceId": "src-stage-1a"},
                {"problemStageId": "ps-1b", "axisCode": "L2", "status": "NOT_PASSED",
                 "questionText": "q2", "interviewSourceId": "src-stage-1b"},
            ],
        },
        {
            "problemNo": 2, "conceptName": "동시성", "conceptNameSource": "TEACHES_CANONICAL_NAME",
            "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
            "interviewSourceId": "src-problem-2",
            "stages": [
                {"problemStageId": "ps-2a", "axisCode": "L2", "status": "PASSED",
                 "questionText": "q3", "interviewSourceId": "src-stage-2a"},
                {"problemStageId": "ps-2b", "axisCode": "L3", "status": "NOT_PASSED",
                 "questionText": "q4", "interviewSourceId": "src-stage-2b"},
            ],
        },
        {
            "problemNo": 3, "conceptName": "예외 처리", "conceptNameSource": "TEACHES_CANONICAL_NAME",
            "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
            "interviewSourceId": "src-problem-3",
            "stages": [
                {"problemStageId": "ps-3a", "axisCode": "L2", "status": "NOT_PASSED",
                 "questionText": "q5", "interviewSourceId": "src-stage-3a"},
            ],
        },
    ]
    req = InterviewBriefRequest.model_validate(req_dict)

    composition, qna_targets = engine._compose(req)

    assert composition.qna == 2  # 문제1의 L2(ps-1b), 문제3의 L2(ps-3a)만
    assert [s.interview_source_id for _, s in qna_targets] == ["src-stage-1b", "src-stage-3a"]


def test_qna_count_is_capped_at_total_eight():
    """고정분(라포1+위험1+일반2=4) + L2 미통과 5개 = 9지만 8개로 잘리고, 앞쪽 문제부터 남는다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"] = [
        {
            "problemNo": n, "conceptName": f"개념{n}", "conceptNameSource": "TEACHES_CANONICAL_NAME",
            "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
            "interviewSourceId": f"src-problem-{n}",
            "stages": [{
                "problemStageId": f"ps-{n}", "axisCode": "L2", "status": "NOT_PASSED",
                "questionText": f"q{n}", "interviewSourceId": f"src-stage-{n}",
            }],
        }
        for n in range(1, 6)
    ]
    req = InterviewBriefRequest.model_validate(req_dict)

    composition, qna_targets = engine._compose(req)

    assert composition.total == 8
    assert composition.qna == 4
    assert [p.problem_no for p, _ in qna_targets] == [1, 2, 3, 4]


def test_prior_interview_category_present_only_when_prior_interviews_exist():
    composition_empty, _ = engine._compose(InterviewBriefRequest.model_validate(_request()))
    assert composition_empty.prior_interview == 0
    assert "PRIOR_INTERVIEW" not in composition_empty.sequence()

    req_dict = _request()
    req_dict["priorInterviews"] = [{
        "completedAt": "2026-07-01T10:00:00Z",
        "resultSummary": "지난번엔 상태 관리 개념을 헷갈려함",
    }]
    composition_with, _ = engine._compose(InterviewBriefRequest.model_validate(req_dict))
    assert composition_with.prior_interview == 1
    assert composition_with.total == composition_empty.total + 1


def test_risk_category_present_only_when_risk_reasons_exist():
    req_dict = _request()
    req_dict["riskReasons"] = []
    composition, _ = engine._compose(InterviewBriefRequest.model_validate(req_dict))

    assert composition.risk == 0
    assert "RISK" not in composition.sequence()
    assert composition.total == 4  # 라포1 + 일반2 + 문답1(기본 L2 미통과 1개)


def test_unknown_question_type_is_rejected(monkeypatch):
    items = _matching_items()
    items[0]["questionType"] = "SMALL_TALK"  # 허용되지 않는 값
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    with pytest.raises(stages.StageError, match="questionType"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_question_type_sequence_mismatch_is_rejected(monkeypatch):
    """개수·전체 종류 구성은 맞아도 순서(RAPPORT->...->QNA)가 뒤바뀌면 걸려야 한다."""
    items = _matching_items()
    items[0]["questionType"], items[2]["questionType"] = items[2]["questionType"], items[0]["questionType"]
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    with pytest.raises(stages.StageError, match="질문 구성/순서가 기대와 다릅니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


# ── 엔진 ──────────────────────────────────────────────────────────────────

def test_generate_happy_path(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "지난달에 얘기 나눴었죠.", "items": _matching_items()})

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert result.opening_remark == "지난달에 얘기 나눴었죠."
    assert len(result.items) == 5
    assert [i.suggested_order for i in result.items] == [1, 2, 3, 4, 5]
    assert [i.question_type for i in result.items] == ["RAPPORT", "RISK", "GENERAL", "GENERAL", "QNA"]


def test_flagged_stage_excluded_from_prompt_and_allowed_ids(monkeypatch):
    """isFlagged=true 단계는 프롬프트 텍스트에도, 허용 interviewSourceId 집합에도 없어야 한다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["stages"].append({
        "problemStageId": "ps-2", "axisCode": "L3", "status": "NOT_REACHED",
        "questionText": "flagged 질문", "isFlagged": True, "interviewSourceId": "src-flagged",
    })
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    comprehension_block = calls[0]["values"]["comprehension_block"]
    assert "src-flagged" not in comprehension_block
    assert "flagged 질문" not in comprehension_block


def test_fabricated_interview_source_id_is_rejected(monkeypatch):
    _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _matching_items(source_id="src-INVENTED"),
    })

    with pytest.raises(stages.StageError, match="지어냈습니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_null_interview_source_id_is_accepted_not_fabrication(monkeypatch):
    """D-ib3(실LLM 호출로 발견): priorInterviews/observationNotes/briefContext는
    명세상 id가 없다. 그 근거만으로 만든 라포 질문이 interviewSourceId를 null로
    두는 건 정직한 미기재이지 위조가 아니다 -- 빈 문자열/None 둘 다 허용해야 한다."""
    items = _matching_items()
    items[0]["interviewSourceId"] = None
    del items[2]["interviewSourceId"]  # 아예 필드 자체를 생략한 경우도 허용
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert result.items[0].interview_source_id is None
    assert result.items[2].interview_source_id is None
    assert result.items[4].interview_source_id == "src-stage-1"  # 나머지는 정상 그대로


def test_too_few_items_is_rejected(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()[:1]})

    with pytest.raises(stages.StageError, match=r"개수가 기대한 구성과 다릅니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_non_contiguous_suggested_order_is_rejected(monkeypatch):
    items = _matching_items()
    for i, item in enumerate(items):
        item["suggestedOrder"] = (i + 1) * 2  # 2, 4, 6, 8, 10
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": items})

    with pytest.raises(stages.StageError, match="연속 정수가 아닙니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))


def test_not_generated_problem_has_no_stages_and_is_not_treated_as_failure(monkeypatch):
    """NOT_GENERATED 문제는 stages가 비어 있고, 미달이 아니라 '안 물어봤다'로 취급된다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0] = {
        "problemNo": 2, "conceptName": "동시성", "conceptNameSource": "TEACHES_CANONICAL_NAME",
        "problemScope": "TEAM_SHARED_PROBLEM",
        "generationStatus": "NOT_GENERATED", "notGeneratedReasonCode": "NO_MATCHING_CODE_EVIDENCE",
        "interviewSourceId": "src-problem-2", "stages": [],
    }
    calls = _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _matching_items(source_id="src-risk-1", qna_count=0),  # L2 미통과 단계가 없음
    })

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
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

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
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

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
        "snippetKey": "submission-1:0", "snippetHash": "sha256:deadbeef",
    }
    calls = _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _matching_items(source_id="src-problem-1"),  # 문제 단위 id로 응답
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
        "problemNo": 2, "conceptName": "동시성", "conceptNameSource": "TEACHES_CANONICAL_NAME",
        "problemScope": "TEAM_SHARED_PROBLEM",
        "generationStatus": "NOT_GENERATED", "notGeneratedReasonCode": "NO_MATCHING_CODE_EVIDENCE",
        "interviewSourceId": "src-problem-2", "stages": [],
    }
    _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _matching_items(source_id="src-problem-2", qna_count=0),
    })

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
        "items": _matching_items(source_id="src-attempt-9", qna_count=0),
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
        "visibility": "MANAGER_ONLY",
    }]
    items = _matching_items()
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
    """conceptNameSource(D-2 대응)가 TEACHES_CANONICAL_NAME가 아니면 확신도를 낮추라는
    지시가 프롬프트에 붙어야 한다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["conceptNameSource"] = "PROBLEM_TITLE"
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["comprehension_block"]
    assert "단정하지 말고" in block


def test_concept_name_source_unavailable_also_hedges(monkeypatch):
    """UNAVAILABLE(title 폴백조차 불가)도 PROBLEM_TITLE과 마찬가지로 여지를 둔
    표현을 강제해야 한다 -- TEACHES_CANONICAL_NAME 하나만 확정 경로다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["conceptNameSource"] = "UNAVAILABLE"
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    assert "단정하지 말고" in calls[0]["values"]["comprehension_block"]


# ── D-ib5 (2026-08-07, 백엔드 재회신으로 D-ib4의 추정값 정정) ────────────────────

def test_concept_name_source_is_required():
    """D-ib4는 optional로 열어뒀는데, D-ib4 자체 커밋 메시지가 이미 '아직 백엔드가
    안 보내도 되게'라고 임시로 인정한 상태였다. 백엔드가 부록A로 4종을 못박은
    이상(D-ib5) 생략은 조용히 None으로 새는 게 아니라 막혀야 한다."""
    from pydantic import ValidationError

    req = _request()
    del req["comprehension"]["problems"][0]["conceptNameSource"]
    with pytest.raises(ValidationError):
        InterviewBriefRequest.model_validate(req)


def test_concept_name_source_rejects_d_ib4_stale_value_names():
    """D-ib4가 썼던 VERIFICATION_CONCEPT/CURRICULUM_EVIDENCE는 D-ib5에서
    TEACHES_CANONICAL_NAME/CURRICULUM_EVIDENCE_TEACHES로 이름이 바뀌었다 --
    옛 이름이 실수로 다시 살아나면 여기서 잡혀야 한다."""
    from pydantic import ValidationError

    req = _request()
    req["comprehension"]["problems"][0]["conceptNameSource"] = "VERIFICATION_CONCEPT"
    with pytest.raises(ValidationError):
        InterviewBriefRequest.model_validate(req)


def test_observation_note_visibility_is_required_and_constrained():
    from pydantic import ValidationError

    from app.schemas.interview_brief import ObservationNote

    with pytest.raises(ValidationError):
        ObservationNote.model_validate({
            "occurredAt": "2026-07-20T10:00:00Z", "content": "메모", "interviewSourceId": "src-note-x",
        })
    with pytest.raises(ValidationError):
        ObservationNote.model_validate({
            "occurredAt": "2026-07-20T10:00:00Z", "content": "메모", "interviewSourceId": "src-note-x",
            "visibility": "PRIVATE",  # D-ib4 이전에 논의됐던 오판 값 -- 값 집합은 MANAGER_ONLY 하나뿐
        })


def test_code_context_requires_snippet_key_and_hash():
    """§3 A-2: 원문 대신 좌표+키+해시 6필드 전부. 신설 2필드 생략 시 막혀야 한다."""
    from pydantic import ValidationError

    req = _request()
    req["comprehension"]["problems"][0]["codeContext"] = {
        "language": "python", "path": "app/foo.py", "lineStart": 10, "lineEnd": 20,
    }
    with pytest.raises(ValidationError):
        InterviewBriefRequest.model_validate(req)


def test_student_answer_text_is_fenced_as_untrusted(monkeypatch):
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    engine.generate(InterviewBriefRequest.model_validate(_request()))

    block = calls[0]["values"]["comprehension_block"]
    assert "answer_START" in block and "answer_END" in block
    assert "잘 모르겠습니다" in block  # 내용 자체는 그대로 전달(차단이 아니라 표시)


# ── 라우터 ────────────────────────────────────────────────────────────────

def test_router_returns_200_with_usage_meta_body(monkeypatch):
    """D-ib2 EXIT(2026-08-07): 백엔드가 본문 단일화로 확정 -- 헤더는 더 이상 없다."""
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["openingRemark"] == "여는 말"
    assert "x-ai-usage-status" not in r.headers
    assert "x-ai-usage-model-code" not in r.headers


def test_router_usage_meta_body_fields(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()}, usages=[{
        "model_code": "test-model", "input_token_count": 500, "output_token_count": 100,
        "cached_token_count": 0, "status": "SUCCEEDED", "failure_code": None,
        "latency_ms": 1200,
    }])

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    meta = r.json()["usageMeta"]
    assert meta["modelCode"] == "test-model"
    assert meta["inputTokenCount"] == 500
    assert meta["outputTokenCount"] == 100
    assert meta["latencyMs"] == 1200
    assert meta["status"] == "SUCCEEDED"
    assert meta["failureCode"] is None


def test_router_returns_503_with_failure_code_on_stage_error(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()[:1]}, usages=[{
        "model_code": "test-model", "input_token_count": 500, "output_token_count": 100,
        "cached_token_count": 0, "status": "SUCCEEDED", "failure_code": None,
        "latency_ms": 900,
    }])

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    assert r.status_code == 503
    body = r.json()
    assert body["failureCode"] == "INVALID_JSON"
    assert "message" in body
    # §3 A-5: 검증 실패도 LLM 호출 자체는 성공했으므로 그 usage를 그대로 싣는다.
    assert body["usageMeta"]["status"] == "SUCCEEDED"
    assert body["usageMeta"]["latencyMs"] == 900


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
    body = r.json()
    assert body["failureCode"] == "RATE_LIMITED"
    # §3 A-5: 실패도 latency_ms NOT NULL로 기록돼야 하므로 usageMeta가 실려야 한다.
    assert body["usageMeta"]["status"] == "FAILED"
    assert body["usageMeta"]["failureCode"] == "RATE_LIMITED"
    assert body["usageMeta"]["latencyMs"] == 300


def test_router_failure_with_no_usages_has_null_usage_meta(monkeypatch):
    """LLM 호출 자체가 시도되기 전에 죽으면(usages가 아예 빈 리스트) 보고할 사용량이
    없다 -- usageMeta 키는 여전히 나가되 값은 null이어야 한다(생략이 아니라 명시적 null)."""
    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        raise stages.StageError("ib-1: 실패", usages=[])

    monkeypatch.setattr(engine.stages, "call", _call)

    r = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=HEADERS)

    body = r.json()
    assert r.status_code == 503
    assert "usageMeta" in body
    assert body["usageMeta"] is None


def test_idempotency_key_reuse_skips_recomputation(monkeypatch):
    """같은 X-Idempotency-Key로 재전송하면 stages.call을 다시 부르지 않는다."""
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})
    headers = {**HEADERS, "X-Idempotency-Key": "interview-1:retry-test"}

    first = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=headers)
    second = client.post("/internal/v1/interview-brief:generate", json=_request(), headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1  # 두 번째 요청은 캐시에서 그대로 반환됐다
