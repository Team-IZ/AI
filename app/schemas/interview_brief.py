""" 면담 브리프 생성 API의 요청/응답 스키마. 표기는 camelCase.

명세: `IZ-Get_면담브리프_생성API_명세서_v08.md` (2026-08-05). `POST /internal/v1/
interview-brief:generate` 하나뿐이고 동기(sync) 계약이다 — job/폴링 없음, 이
요청이 곧 그 응답이다. AI는 DB에 직접 접근하지 않는다 — target/riskReasons/
comprehension 등은 전부 백엔드가 조립해서 실어 보낸 값이고, AI는 그걸 그대로
믿지 않고(모델이 되돌려주는 interviewSourceId만 예외 없이 검증한다 -- engine 쪽)
가공해서 질문을 만든다.

부분 성공 없음(§5.2): 여는 말만 되고 체크리스트가 실패해도 전체 실패로 처리된다.
"""
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import BaseSchema

# §9 부록 코드값 그대로. Spring이 이미 DB CHECK로 강제하는 값이라 AI가 별도로
# 좁히지 않는다 -- 모르는 값이 오면 그냥 Literal 검증 실패로 422가 나가는 편이
# "그런 값도 있나보다" 하고 조용히 넘기는 것보다 안전하다.
ProjectCategory = Literal["MINI_PROJECT", "BIG_PROJECT"]
BriefType = Literal["STANDARD", "INVALID_ATTEMPT"]
RiskReasonCode = Literal[
    "STAGE_DECLINE", "PERSISTENT_LOW", "INVALID_ATTEMPT",
    "CONTRIBUTION_UNDERSTANDING_GAP", "LOW_PARTICIPATION",
]
EvaluationStatus = Literal["MATCHED", "NOT_MATCHED", "NOT_APPLICABLE", "UNAVAILABLE"]
NotApplicableReasonCode = Literal["FIRST_MINI_PROJECT", "INSUFFICIENT_LONGITUDINAL_HISTORY"]
ValidityReviewStatus = Literal["NOT_REQUIRED", "PENDING", "CONFIRMED_INVALID", "RESTORED_VALID"]
AttemptType = Literal["INITIAL", "RETRY", "REVIEW"]
AttemptStatus = Literal[
    "NOT_STARTED", "SUBMITTED", "ANALYZING", "SESSION_READY",
    "SESSION_IN_PROGRESS", "COMPLETED", "FAILED", "EXPIRED",
]
TerminalReasonCode = Literal[
    "COMPLETED", "NOT_SUBMITTED", "ANALYSIS_FAILED",
    "INSUFFICIENT_PROBLEM_EVIDENCE", "INSUFFICIENT_OWN_COMMIT_EVIDENCE",
    "NOT_ATTENDED", "SESSION_INCOMPLETE", "REVIEW_NOT_COMPLETED", "INVALID",
]
SessionEndReasonCode = Literal[
    "ALL_PROBLEMS_TERMINAL", "ALL_REVIEW_TARGETS_TERMINAL", "COMPLETED_L4",
    "TERMINATED_AT_L1", "TERMINATED_AT_L2", "TERMINATED_AT_L3", "TERMINATED_AT_L4",
    "POLICY_TIME_LIMIT_EXCEEDED", "ASSESSMENT_WINDOW_EXPIRED",
]
ProblemScope = Literal["TEAM_SHARED_PROBLEM", "INDIVIDUAL_OWN_COMMIT"]
GenerationStatus = Literal["GENERATED", "NOT_GENERATED"]
NotGeneratedReasonCode = Literal["NO_MATCHING_CODE_EVIDENCE"]
StageStatus = Literal[
    "PREPARED", "IN_PROGRESS", "PASSED", "NOT_PASSED", "NOT_REACHED", "NOT_ANSWERED",
]
AxisCode = Literal["L1", "L2", "L3", "L4"]
BestSuccessStage = Literal["L1", "L2", "L3", "L4"]


class Target(BaseSchema):
    """면담 여는 말의 호칭·상황 설명 재료. §4.1 ①."""

    user_name: str
    class_name: str
    project_name: str
    project_category: ProjectCategory
    round_name: str
    analysis_role_code: str | None = Field(
        default=None,
        description="빅프로젝트에서 회차별 분석 역할 구분. 미니프로젝트는 없다",
    )


class BriefContext(BaseSchema):
    """여는 말 톤과 질문 수를 가르는 스위치. §4.1 ②."""

    brief_type: BriefType
    is_first_interview: bool = Field(
        description="true면 라포 형성용 도입 질문을 반드시 1개 이상 포함해야 한다 "
                    "(§6.2). priorInterviews/observationNotes가 비어 있다",
    )


class RiskReason(BaseSchema):
    """왜 이 교육생이 면담 대상이 됐는가. §4.1 ③.

    한 교육생에게 복수 사유가 동시에 있을 수 있다(리스트). `evaluationStatus`가
    NOT_APPLICABLE/UNAVAILABLE이면 **"문제 없음"이 아니라 "이번엔 판단 불가"로
    서술해야 한다** — 이건 데이터가 아니라 프롬프트 지시라 engine 쪽에서 강제한다.
    """

    reason_code: RiskReasonCode
    evaluation_status: EvaluationStatus
    not_applicable_reason_code: NotApplicableReasonCode | None = None
    reason_summary: str
    detected_at: datetime
    source_problem_stage_id: str | None = Field(
        default=None,
        description="이 위험의 근거가 된 정확한 문제 단계. CONTRIBUTION_UNDERSTANDING_GAP·"
                    "LOW_PARTICIPATION처럼 단일 단계로 못 좁히는 사유는 없을 수 있다",
    )
    source_interview_source_id: str = Field(
        description="★ 출력에서 이 사유를 근거로 질문을 만들면 이 값을 그대로 실어야 "
                    "한다(engine이 대조·검증)",
    )


class ValidityReview(BaseSchema):
    """응시 자체를 인정할 것인가. §4.1 ④."""

    status: ValidityReviewStatus
    decision_reason_code: str | None = None
    decision_note: str | None = Field(
        default=None, description="매니저가 남긴 판정 메모",
    )


class ComprehensionCodeContext(BaseSchema):
    """문제 근거 코드의 위치만. **원문은 전달하지 않는다**(§4.1 명시)."""

    language: str
    path: str
    line_start: int
    line_end: int


class ComprehensionStage(BaseSchema):
    """`comprehension.problems[].stages[]` 원소 하나. problem_stage 한 행에 대응.

    질문·힌트1·힌트2 세 슬롯 중 답한 만큼만 채워진다 -- 세션(develop 쪽 app/sessions.py)
    과 동일한 "슬롯은 hintsUsed가 정한다" 구조.
    """

    problem_stage_id: str
    axis_code: AxisCode
    status: StageStatus
    question_text: str
    question_answer_text: str | None = None
    question_score: int | None = Field(default=None, ge=0, le=5)
    question_passed: bool | None = None
    first_hint_text: str | None = None
    first_hint_answer_text: str | None = None
    first_hint_score: int | None = Field(default=None, ge=0, le=5)
    first_hint_passed: bool | None = None
    second_hint_text: str | None = None
    second_hint_answer_text: str | None = None
    second_hint_score: int | None = Field(default=None, ge=0, le=5)
    second_hint_passed: bool | None = None
    is_flagged: bool = Field(
        default=False,
        description="질문 자체가 이상해 재생성에도 실패한 단계. true면 근거로 삼지 않는다"
                    "(engine이 강제 제외)",
    )
    interview_source_id: str = Field(
        description="★ 이 단계를 근거로 질문을 만들면 이 값을 그대로 실어야 한다",
    )


class ProblemComprehension(BaseSchema):
    """`comprehension.problems[]` 원소 하나. assessment_problem 한 건에 대응."""

    problem_no: int = Field(ge=1)
    concept_name: str = Field(
        description="검증 개념 표시명. ★ 질문에서 L2 같은 내부 코드 대신 이 이름을 쓴다",
    )
    problem_scope: ProblemScope
    generation_status: GenerationStatus
    not_generated_reason_code: NotGeneratedReasonCode | None = None
    best_success_stage: BestSuccessStage | None = None
    code_context: ComprehensionCodeContext | None = Field(
        default=None, description="NOT_GENERATED 문제는 없다",
    )
    stages: list[ComprehensionStage] = Field(
        default_factory=list,
        description="NOT_GENERATED 문제는 빈 배열 -- 0점·미달이 아니라 애초에 안 물어봤다",
    )


class Comprehension(BaseSchema):
    """코드 이해도 검증 결과. 질문의 1차 재료. §4.1 ⑤."""

    attempt_type: AttemptType
    attempt_status: AttemptStatus
    terminal_reason_code: TerminalReasonCode = Field(
        description="NOT_ATTENDED면 이해도 질문 자체를 만들 수 없다 -- 미응시 사유를 "
                    "묻는 질문으로 전환해야 한다(engine이 problems 공백과 함께 감지)",
    )
    session_end_reason_code: SessionEndReasonCode | None = Field(
        default=None, description="세션이 실제로 열리지 않았으면(NOT_ATTENDED 등) 없다",
    )
    problems: list[ProblemComprehension] = Field(
        default_factory=list,
        description="미응시(terminalReasonCode='NOT_ATTENDED')면 빈 배열",
    )


class PriorInterviewActivity(BaseSchema):
    """이전 면담 중 관찰·확인 사항 한 건."""

    content: str
    next_action: str | None = None
    occurred_at: datetime


class PriorInterview(BaseSchema):
    """이전 면담 기록 한 건. `isFirstInterview=true`면 빈 배열."""

    completed_at: datetime
    result_summary: str
    activities: list[PriorInterviewActivity] = Field(default_factory=list)
    asked_questions: list[str] = Field(
        default_factory=list,
        description="지난 회차에 실제로 던진 질문 원문. ★ 같은 질문을 반복하지 말고 "
                    "후속 질문으로 발전시켜야 한다",
    )


class ObservationNote(BaseSchema):
    """면담과 무관하게 매니저가 남긴 일반 관찰 메모. §4.1 ⑦."""

    occurred_at: datetime
    content: str = Field(
        description="학생 발화가 원문 그대로 인용될 수 있다(예: 쉬는 시간 발언). "
                    "학생 답변 텍스트와 같은 급의 신뢰 불가 데이터 -- engine이 감싼다",
    )


class InterviewBriefRequest(BaseSchema):
    """POST /internal/v1/interview-brief:generate 요청 본문 전체."""

    target: Target
    brief_context: BriefContext
    risk_reasons: list[RiskReason] = Field(default_factory=list)
    validity_review: ValidityReview
    comprehension: Comprehension
    prior_interviews: list[PriorInterview] = Field(default_factory=list)
    observation_notes: list[ObservationNote] = Field(default_factory=list)


class InterviewBriefItem(BaseSchema):
    """체크리스트 항목 하나. §5."""

    question_text: str = Field(
        description="매니저가 그대로 읽을 구어체 질문 한 문장. 물음표로 끝난다",
    )
    question_rationale: str = Field(
        description="매니저만 보는 근거. 어떤 데이터에서 나왔는지 명시",
    )
    suggested_order: int = Field(ge=1)
    interview_source_id: str | None = Field(
        default=None,
        description="요청에서 받은 값 중 하나여야 한다 -- 새 UUID면 백엔드가 저장을 거부한다. "
                    "priorInterviews/observationNotes/briefContext는 명세상 id 자체가 없어 "
                    "그 근거만으로 만든 라포 질문은 null이 정상이다(engine이 지어낸 값과 "
                    "정직한 null을 구분해서 검증한다)",
    )


class UsageMeta(BaseSchema):
    """§7의 AI 제공 사용량 값(model_code·토큰 3종·latency·status·failureCode)을
    본문에도 싣는다. `app/api/interview_brief.py`의 D-ib2 주석 참고 -- 응답 헤더
    (X-Ai-Usage-*)와 완전히 같은 값을 두 군데에 싣는 임시 조치다."""

    model_code: str
    input_token_count: int = Field(ge=0)
    output_token_count: int = Field(ge=0)
    cached_token_count: int = Field(default=0, ge=0)
    latency_ms: int = Field(ge=0)
    status: Literal["SUCCEEDED", "FAILED", "PARTIAL"]
    failure_code: str | None = None


class InterviewBriefResponse(BaseSchema):
    """성공 응답 본문. §5. jobId 없음 -- 이 응답이 곧 결과다.

    `usageMeta`는 §5 명세 예시엔 없는 필드다 -- D-ib2(app/api/interview_brief.py) 참고.
    """

    opening_remark: str = Field(
        description="1~3문장, 구어체. 교육생 이름은 부르되 점수·단계·위험 유형은 "
                    "직접 언급하지 않는다",
    )
    items: list[InterviewBriefItem] = Field(
        min_length=4, max_length=8,
        description="4~8개(첫 면담이면 6~8개 -- engine이 강제). suggestedOrder는 "
                    "1부터 중복 없는 연속 정수여야 한다",
    )
    usage_meta: UsageMeta | None = Field(
        default=None,
        description="응답 헤더(X-Ai-Usage-*)와 동일한 값의 본문 사본. 헤더/본문 "
                    "어느 쪽을 쓸지 백엔드가 정하기 전까지 둘 다 채운다",
    )
