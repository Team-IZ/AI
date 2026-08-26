""" 면담 브리프 생성(ib-1) 스키마·엔진·라우터 테스트.

명세: IZ-Get_면담브리프_생성API_명세서_v08.md. 부분 성공 없음(§5.2) -- 검증에 하나라도
걸리면 engine.generate()가 StageError를 올리고 라우터가 503+failureCode로 바꾼다.

2026-08-12: 질문 개수 규칙이 "4~8개(첫 면담이면 6~8개)"에서 5-카테고리 고정 구성(라포1 +
이전면담0~1 + 위험0~1 + 일반2 + 문답N, N은 8개 상한에 맞춰 절삭)으로 바뀌었다. `_request()`
기본 픽스처(위험 사유 1개, priorInterviews=[], L2/NOT_PASSED 단계 1개)의 기대 구성은
RAPPORT, RISK, GENERAL, GENERAL, QNA 5개 -- `_matching_items()`가 그 구성에 맞는 items를
만들어준다.

2026-08-26(D-ib6/D-ib7): 실서비스 503 인시던트(모델이 suggestedOrder·questionType·
interviewSourceId를 스스로 채우다 반복적으로 계약을 어김) 이후, 이 세 필드는 더 이상
모델 출력에서 읽지 않는다 -- 서버가 질문 구성(`_Composition.sequence()`)과 요청 데이터로
결정론적으로 채운다. 모델은 이제 `questionText`/`questionRationale`만 낸다. 그래서 옛
"모델이 questionType/suggestedOrder/interviewSourceId를 어겼다" 계열 테스트는 대상
코드 자체가 없어져 삭제하고, 그 자리는 "서버가 카테고리별로 정확한 근거ID를 배정하는지"를
검증하는 테스트로 바뀌었다.
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
        "briefId": "11111111-2222-3333-4444-555555555555",
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
    *, prior_interview: bool = False, risk: bool = True, qna_count: int = 1,
) -> list[dict]:
    """요청 픽스처의 실제 구성(라포1 + 이전면담0/1 + 위험0/1 + 일반2 + 문답N)과 정확히
    맞아떨어지는 개수의 items를 만든다. 기본값(prior_interview=False, risk=True, qna_count=1)은
    `_request()`의 기본 구성(RAPPORT, RISK, GENERAL, GENERAL, QNA 1개)과 같다.

    D-ib6(2026-08-26) 이후 모델은 questionText/questionRationale만 낸다 -- suggestedOrder·
    questionType·interviewSourceId는 서버가 배열 위치와 `_Composition.sequence()`로 채우므로
    여기서 만들 필요가 없다."""
    count = 1 + (1 if prior_interview else 0) + (1 if risk else 0) + 2 + qna_count
    return [
        {"questionText": f"질문 {i}?", "questionRationale": f"근거{i}"}
        for i in range(1, count + 1)
    ]


def _stub_call(monkeypatch, data: dict, usages: list[dict] | None = None):
    """가짜 LLM을 꽂는다.

    🔴 `engine_mode`도 real로 고정한다(2026-08-12). 기본값(stub)이면 서비스 계층
    (`app/interview_brief.py`)이 엔진을 아예 안 부르고 자체 스텁 응답으로 떨어져서,
    여기서 꽂은 데이터가 조용히 무시된다 — 재는 대상은 **실경로**다.
    """
    monkeypatch.setattr(get_settings(), "engine_mode", "real")
    calls = []

    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        calls.append({"stage_id": stage_id, "values": values, "extra_user": extra_user})
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
            "items": [
                {"questionText": "q1?", "questionRationale": "r1",
                 "suggestedOrder": 1, "questionType": "RAPPORT", "interviewSourceId": None},
                {"questionText": "q2?", "questionRationale": "r2",
                 "suggestedOrder": 2, "questionType": "GENERAL", "interviewSourceId": None},
            ],  # 2개, 스키마 하한(3) 위반
        })


# ── 질문 구성(2026-08-12: 5-카테고리 고정 순서) ──────────────────────────────

def test_qna_targets_are_the_terminal_stage_of_each_problem_whatever_the_axis():
    """🔴 축과 무관하게 NOT_PASSED 단계를 센다(2026-08-12, L2 필터 제거).

    `NOT_PASSED`는 "이 축에서 문제가 끝났다"라 문제당 최대 1개이고, 그 뒤 축은
    `NOT_REACHED`다. L2로 좁히면 **L1에서 끝난 학생(문제 1)의 문답 질문이 0개**가
    되는데 그 학생이야말로 면담 1순위다. 완주한 문제(전부 PASSED)는 안 센다.
    """
    def _problem(no, concept, stages):
        return {
            "problemNo": no, "conceptName": concept,
            "conceptNameSource": "TEACHES_CANONICAL_NAME",
            "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
            "interviewSourceId": f"src-problem-{no}", "stages": stages,
        }

    def _stage(sid, axis, status):
        return {"problemStageId": sid, "axisCode": axis, "status": status,
                "questionText": f"q-{sid}", "interviewSourceId": f"src-stage-{sid}"}

    req_dict = _request()
    req_dict["comprehension"]["problems"] = [
        # L1에서 끝났다 -- 옛 L2 필터에서는 통째로 빠지던 자리
        _problem(1, "상태 관리", [
            _stage("1a", "L1", "NOT_PASSED"),
            _stage("1b", "L2", "NOT_REACHED"),
        ]),
        # 완주 -- 셀 게 없다
        _problem(2, "동시성", [
            _stage("2a", "L1", "PASSED"),
            _stage("2b", "L2", "PASSED"),
        ]),
        # L3까지 가서 끝났다
        _problem(3, "예외 처리", [
            _stage("3a", "L1", "PASSED"),
            _stage("3b", "L2", "PASSED"),
            _stage("3c", "L3", "NOT_PASSED"),
            _stage("3d", "L4", "NOT_REACHED"),
        ]),
    ]
    req = InterviewBriefRequest.model_validate(req_dict)

    composition, qna_targets = engine._compose(req)

    assert composition.qna == 2
    assert [s.interview_source_id for _, s in qna_targets] == ["src-stage-1a", "src-stage-3c"]


def test_flagged_stage_is_not_a_qna_target():
    """isFlagged 단계는 프롬프트에서 빠지므로 근거로도 쓸 수 없다 -- 세면 존재하지
    않는 근거를 가리키는 QNA 질문이 계획에 들어간다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["stages"][0]["isFlagged"] = True
    req = InterviewBriefRequest.model_validate(req_dict)

    composition, qna_targets = engine._compose(req)

    assert composition.qna == 0
    assert qna_targets == []


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


# ── D-ib6(2026-08-26): 서버가 순서·분류·근거ID를 결정한다 ─────────────────────
#
# 모델이 더 이상 suggestedOrder/questionType/interviewSourceId를 내지 않는다(실서비스
# 503 인시던트 이후). 배열 위치가 곧 순서·분류이고(`test_generate_happy_path`가 이미
# 확인한다), 근거ID는 카테고리별로 서버가 안다: RAPPORT/PRIOR_INTERVIEW/GENERAL은
# `_anchor_source_id`, RISK는 유일한 위험 사유, QNA는 `qna_targets`와 순서대로 1:1
# 대응(`_resolve_source_id`).

def test_risk_item_is_anchored_to_the_only_risk_reason(monkeypatch):
    """RISK 카테고리는 최대 1건(`_compose` 참고)이라 서버가 그 위험 사유의
    interviewSourceId를 그대로 배정한다 -- 모델은 이제 이 값을 내지 않는다."""
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    risk_item = result.items[[i.question_type for i in result.items].index("RISK")]
    assert risk_item.interview_source_id == "src-risk-1"


def test_qna_items_are_anchored_in_order_to_qna_targets(monkeypatch):
    """QNA 카테고리는 `qna_targets`와 순서대로 1:1 대응한다."""
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
        for n in (1, 2)
    ]
    _stub_call(monkeypatch, {
        "openingRemark": "여는 말", "items": _matching_items(qna_count=2),
    })

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    qna_items = [i for i in result.items if i.question_type == "QNA"]
    assert [i.interview_source_id for i in qna_items] == ["src-stage-1", "src-stage-2"]


def test_evidenceless_item_is_anchored_not_dropped(monkeypatch):
    """🔴 근거 없는 항목을 버리지도, null로 내보내지도 않는다(2026-08-15 백엔드 합의,
    D-ib6 이후로도 그대로 유지 -- 이제 애초에 모델에게 근거를 묻지 않으니 "근거 없음"은
    RAPPORT/PRIOR_INTERVIEW/GENERAL 카테고리 자체의 속성이다).

    라포·일반 질문은 설계상 근거가 없다 -- 그래도 interviewSourceId는 항상 채워진다
    (`_anchor_source_id`가 attempt 단위로 앵커링).
    """
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert len(result.items) == 5                                   # 하나도 안 버린다
    assert all(i.interview_source_id for i in result.items)         # null이 나가지 않는다
    # 픽스처에 관찰 메모가 없어 RAPPORT도 attempt로 떨어진다.
    general_items = [i for i in result.items if i.question_type == "GENERAL"]
    assert all(i.interview_source_id == "src-attempt-1" for i in general_items)
    assert result.items[0].interview_source_id == "src-attempt-1"   # RAPPORT


def test_rapport_anchors_to_the_only_observation_note(monkeypatch):
    """관찰 메모가 정확히 1건이면 라포 질문의 앵커는 그 메모다.

    프롬프트(`rapport_hint`)가 메모를 근거로 라포 질문을 만들게 하므로 실제 출처가
    맞다. 2건 이상이면 어느 것을 썼는지 알 수 없어 attempt로 떨어진다.
    """
    req = _request(observationNotes=[{
        "occurredAt": "2026-08-14T10:00:00Z",
        "content": "팀원과 역할 분담이 애매하다고 했다",
        "interviewSourceId": "src-note-1",
        "visibility": "MANAGER_ONLY",
    }])
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    result = engine.generate(InterviewBriefRequest.model_validate(req))

    assert result.items[0].question_type == "RAPPORT"
    assert result.items[0].interview_source_id == req["observationNotes"][0]["interviewSourceId"]


def test_too_few_items_is_rejected(monkeypatch):
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()[:1]})

    with pytest.raises(stages.StageError, match=r"개수가 기대한 구성과 다릅니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))

    # D-ib7: 검증 실패는 피드백과 함께 1회 재생성한다 -- 재시도까지 포함해 2회 호출된다.
    assert len(calls) == 2
    assert calls[1]["extra_user"] != ""


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
    calls = _stub_call(monkeypatch, {
        "openingRemark": "여는 말",
        "items": _matching_items(qna_count=0),  # L2 미통과 단계가 없음
    })

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    comprehension_block = calls[0]["values"]["comprehension_block"]
    assert "출제되지 않았다" in comprehension_block
    assert "미달로 해석하지" in comprehension_block


# ── D-ib7(2026-08-26): 의미 검증 실패는 1회 피드백 재생성 ─────────────────────

def test_validation_failure_retries_once_with_feedback_and_can_succeed(monkeypatch):
    """첫 응답이 개수를 어겨도, 오류를 알려주고 다시 부른 두 번째 응답이 맞으면 성공한다."""
    monkeypatch.setattr(get_settings(), "engine_mode", "real")
    calls = []

    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        calls.append(extra_user)
        items = _matching_items()[:1] if len(calls) == 1 else _matching_items()
        return stages.StageResult(
            data={"openingRemark": "여는 말", "items": items},
            usages=[{"model_code": model_code, "input_token_count": 10, "output_token_count": 10,
                     "cached_token_count": 0, "status": "SUCCEEDED", "failure_code": None,
                     "latency_ms": 100, "occurred_at": "2026-08-07T09:00:00Z"}],
        )

    monkeypatch.setattr(engine.stages, "call", _call)

    result = engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert len(result.items) == 5
    assert len(calls) == 2
    assert calls[0] == ""            # 첫 시도는 피드백 없이
    assert "거부됨" in calls[1]       # 두 번째 시도는 실패 사유를 담고 있다
    # 실패한 첫 시도의 토큰도 원장에서 사라지면 안 된다.
    assert len(result.usages) == 2


def test_validation_failure_exhausts_after_one_retry(monkeypatch):
    """두 번째도 틀리면 그때는 진짜로 실패한다 -- 무한 재시도가 아니다."""
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()[:1]})

    with pytest.raises(stages.StageError, match=r"개수가 기대한 구성과 다릅니다"):
        engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert len(calls) == 2  # 1회 재시도까지만


def test_transport_failure_is_not_retried_by_the_validation_loop(monkeypatch):
    """stages.call() 자체가 이미 소진한 전송 실패(예: RATE_LIMITED)는 검증 재시도
    루프가 또 부르지 않는다 -- 예산이 이미 그 안에서 다 쓰였다."""
    calls = []

    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        calls.append(extra_user)
        raise stages.StageError("ib-1: 실패", usages=[{
            "model_code": model_code, "input_token_count": 10, "output_token_count": 0,
            "cached_token_count": 0, "status": "FAILED", "failure_code": "RATE_LIMITED",
            "latency_ms": 300, "occurred_at": "2026-08-07T09:00:00Z",
        }])

    monkeypatch.setattr(get_settings(), "engine_mode", "real")
    monkeypatch.setattr(engine.stages, "call", _call)

    with pytest.raises(stages.StageError):
        engine.generate(InterviewBriefRequest.model_validate(_request()))

    assert len(calls) == 1


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
        "triggerReasonCode": "EXCESSIVE_CONNECTION_LOSS",
        "decisionReasonCode": "REVIEWED_VIOLATION_CONFIRMED",
        "decisionNote": "표절 확인됨",
    }
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["validity_review_block"]
    assert "EXCESSIVE_CONNECTION_LOSS" in block
    assert "REVIEWED_VIOLATION_CONFIRMED" in block
    assert "표절 확인됨" in block


def test_code_context_and_problem_interview_source_id_reach_prompt(monkeypatch):
    """codeContext(기존 미사용)가 프롬프트에 실리고, 문제 단위 interviewSourceId가
    QNA 앵커로 정확히 쓰이는지."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["codeContext"] = {
        "language": "python", "path": "app/handlers.py", "lineStart": 10, "lineEnd": 20,
        # A-2의 6필드. 원문 대신 좌표+키+해시만 온다.
        "snippetKey": "snip-1", "snippetHash": "sha256:abc",
    }
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["comprehension_block"]
    assert "app/handlers.py:10-20" in block
    qna_item = result.items[[i.question_type for i in result.items].index("QNA")]
    assert qna_item.interview_source_id == "src-stage-1"  # qna_targets가 가리키는 단계 id


def test_not_generated_problem_interview_source_id_reaches_prompt_with_empty_stages(monkeypatch):
    """NOT_GENERATED 문제(stages=[])도 problem 단위 id는 프롬프트에 실려야 한다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0] = {
        "problemNo": 2, "conceptName": "동시성", "problemScope": "TEAM_SHARED_PROBLEM",
        # NOT_GENERATED는 title조차 CHECK로 NULL이라 개념명 출처가 UNAVAILABLE이다.
        "conceptNameSource": "UNAVAILABLE",
        "generationStatus": "NOT_GENERATED", "notGeneratedReasonCode": "NO_MATCHING_CODE_EVIDENCE",
        "interviewSourceId": "src-problem-2", "stages": [],
    }
    calls = _stub_call(monkeypatch, {
        "openingRemark": "여는 말", "items": _matching_items(qna_count=0),
    })

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    assert "src-problem-2" in calls[0]["values"]["comprehension_block"]


def test_attempt_and_session_interview_source_id_reach_prompt(monkeypatch):
    """시도/세션 단위 interviewSourceId(NOT_ATTENDED 등 problems=[] 케이스의 유일한
    근거)가 프롬프트에 실리고, 근거 없는 카테고리(RAPPORT/GENERAL)가 그 attempt id로
    앵커링되는지."""
    req_dict = _request()
    req_dict["comprehension"] = {
        "attemptType": "INITIAL", "attemptStatus": "FAILED",
        "terminalReasonCode": "NOT_ATTENDED",
        "attemptInterviewSourceId": "src-attempt-9",
        "problems": [],
    }
    calls = _stub_call(monkeypatch, {
        "openingRemark": "여는 말", "items": _matching_items(qna_count=0),
    })

    result = engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["comprehension_block"]
    assert "src-attempt-9" in block
    assert result.items[0].interview_source_id == "src-attempt-9"


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
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    block = calls[0]["values"]["comprehension_block"]
    assert "단정하지 말고" in block


def test_concept_name_source_unavailable_also_hedges(monkeypatch):
    """UNAVAILABLE(title 폴백조차 불가)은 PROBLEM_TITLE보다 한 단계 더 세게 막는다 --
    "여지를 둔 표현"이 아니라 개념 이름 자체를 쓰지 말라는 지시다. concept_name이 아예
    null로 오는 경우라 완곡하게 말할 이름조차 없다."""
    req_dict = _request()
    req_dict["comprehension"]["problems"][0]["conceptNameSource"] = "UNAVAILABLE"
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    engine.generate(InterviewBriefRequest.model_validate(req_dict))

    assert "개념 이름을 지어내지 말고" in calls[0]["values"]["comprehension_block"]


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

BRIEF_PATH = "/api/v0/interview-briefs"
# 멱등키는 이 경로에서 **필수**다 -- ai_usage.idempotency_key가 전역 UNIQUE인데
# briefId 하나로는 재생성(version_no/SUPERSEDED)을 구분할 수 없다.
# 테스트마다 다른 값을 써야 캐시가 안 겹친다.
KEY_HEADERS = {**HEADERS, "Idempotency-Key": "interview-1:default"}


def test_router_returns_200_with_ai_usage_in_the_body(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

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
    # contextId = 요청의 briefId 를 그대로 에코한다(2026-08-07 확정).
    assert usage["contextId"] == "11111111-2222-3333-4444-555555555555"
    assert usage["traceId"] == "trace-1"
    # request_id는 contextId(=briefId)로 폴백한다 -- 다른 네 엔드포인트도 같다
    # (분석은 jobId). 백엔드가 §7에서 "request_id는 이미 아는 값"이라 했으니 저장 시
    # 자기 값으로 덮는다.
    assert usage["requestId"] == "11111111-2222-3333-4444-555555555555"
    assert usage["idempotencyKey"] == "interview-1:200:INTERVIEW_BRIEF:1"
    assert usage["status"] == "SUCCEEDED"
    assert usage["modelCode"]
    # 옛 D-ib2의 헤더 이중 전송은 폐기됐다.
    assert "x-ai-usage-status" not in r.headers


def test_router_requires_the_idempotency_key(monkeypatch):
    """멱등키가 없으면 ai_usage.idempotency_key가 ':INTERVIEW_BRIEF:1'이 되어
    두 번째 호출이 전역 UNIQUE에 걸린다 -- 그 전에 422로 막는다."""
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})

    r = client.post(BRIEF_PATH, json=_request(), headers=HEADERS)

    assert r.status_code == 422


def test_router_returns_503_with_failure_code_on_stage_error(monkeypatch):
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()[:1]})

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

    D-ib7(2026-08-26): 검증 실패는 1회 재시도하므로, 계속 실패하면 두 시도 모두의
    토큰이 남아야 한다(2건) -- 재시도로 태운 비용이 조용히 사라지면 안 된다.
    """
    # LLM 호출은 성공했고(토큰을 태웠고) 그 뒤 계약 검증에서 걸린 경우
    _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()[:1]})

    r = client.post(BRIEF_PATH, json=_request(),
                    headers={**KEY_HEADERS, "Idempotency-Key": "interview-1:usage-on-fail"})

    assert r.status_code == 503
    usage = r.json()["aiUsage"]
    assert len(usage) == 2
    for entry, seq in zip(usage, (1, 2)):
        assert entry["featureCode"] == "INTERVIEW_BRIEF_GENERATION"
        assert entry["inputTokenCount"] == 500
        assert entry["idempotencyKey"] == f"interview-1:usage-on-fail:INTERVIEW_BRIEF:{seq}"


def test_failure_envelope_has_an_empty_usage_list_when_nothing_was_burned(monkeypatch):
    """LLM을 부르기도 전에 죽었으면 빈 배열이다 -- 없는 호출을 지어내지 않는다."""
    def _call(stage_id, values, *, model_code, timeout_s=None, max_attempts=None, extra_user=""):
        raise stages.StageError("ib-1: 프롬프트 조립 실패", usages=[])

    monkeypatch.setattr(get_settings(), "engine_mode", "real")   # _stub_call과 같은 이유
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

    monkeypatch.setattr(get_settings(), "engine_mode", "real")   # _stub_call과 같은 이유
    monkeypatch.setattr(engine.stages, "call", _call)

    r = client.post(BRIEF_PATH, json=_request(),
                    headers={**KEY_HEADERS, "Idempotency-Key": "interview-1:rate"})

    assert r.status_code == 503
    assert r.json()["failureCode"] == "RATE_LIMITED"


def test_idempotency_key_reuse_skips_recomputation(monkeypatch):
    """같은 멱등키 + 같은 본문 재전송이면 stages.call을 다시 부르지 않는다."""
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})
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
    calls = _stub_call(monkeypatch, {"openingRemark": "여는 말", "items": _matching_items()})
    headers = {**HEADERS, "Idempotency-Key": "interview-1:collision"}

    first = client.post(BRIEF_PATH, json=_request(), headers=headers)

    other = _request()
    other["target"]["userName"] = "다른 교육생"
    second = client.post(BRIEF_PATH, json=other, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"] == "IDEMPOTENCY_CONFLICT"
    assert len(calls) == 1  # 두 번째는 아예 생성하지 않았다
