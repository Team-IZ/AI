""" 면담 브리프 생성 API의 요청/응답 스키마. 표기는 camelCase.

명세: `IZ-Get_면담브리프_생성API_명세서_v08.md` (2026-08-05) + 백엔드 감사 회신
`면담_브리프_API_감사_회신에대한_회신.md` (2026-08-07 — 값 집합 3건이 이걸로 정정됐다).
`POST /api/v0/interview-briefs` 하나뿐이고 동기(sync) 계약이다 — job/폴링 없음, 이
요청이 곧 그 응답이다. AI는 DB에 직접 접근하지 않는다 — target/riskReasons/
comprehension 등은 전부 백엔드가 조립해서 실어 보낸 값이고, AI는 그걸 그대로
믿지 않고(모델이 되돌려주는 interviewSourceId만 예외 없이 검증한다 -- engine 쪽)
가공해서 질문을 만든다.

부분 성공 없음(§5.2): 여는 말만 되고 체크리스트가 실패해도 전체 실패로 처리된다.
"""
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import BaseSchema, UuidStr
from app.schemas.usage import AiUsage

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
# 백엔드 회신 §1-4에서 DDL CHECK 2종으로 확정. 3종 이상이면 저장이 거부된다.
NotApplicableReasonCode = Literal["FIRST_MINI_PROJECT", "INSUFFICIENT_LONGITUDINAL_HISTORY"]

# 백엔드 회신 §1-5(2026-08-07)에서 확정. `MANAGER_ONLY` 단일값이고 플랫폼 어휘
# (`assessment_round_question_focus.display_scope`)와도 일치한다. `AUTHOR_ONLY`는
# 정책 확인 중이라 아직 넣지 않는다.
#
# ⚠️ **값으로 필터링하지 않는다**(§3 A-6). 어떤 메모를 브리프에 쓸 수 있는지는
# org/cohort 테넌시와 `manager_assignment` 유효성이 걸린 판단이고 그 맥락은 백엔드에만
# 있다 -- 백엔드가 요청 시점에 걸러 보낸다. 여기서는 프롬프트 톤 조절용 참고값이다.
ObservationNoteVisibility = Literal["MANAGER_ONLY"]

# 백엔드 회신 §1-3 + 부록 A의 COALESCE 체인이 그대로 이 4종이다.
#
# 🔴 옛 값(`VERIFICATION_CONCEPT`·`CURRICULUM_EVIDENCE`)은 **실재하지 않았다** --
# DDL만 보고 추측한 이름이었고 백엔드가 조인 쿼리 원문으로 정정했다. 그리고 문제 하나는
# 반드시 이 넷 중 하나로 귀결되므로(부록 A의 CASE에 ELSE가 있다) **선택이 아니라 필수**다.
ConceptNameSource = Literal[
    "TEACHES_CANONICAL_NAME",       # ① 팀 공통 문제 -- 검증 개념 경로. CHECK가 NOT NULL 강제라 항상 성공
    "CURRICULUM_EVIDENCE_TEACHES",  # ② 개인 커밋 문제 -- 교안 역참조 근거(S-06). ③보다 먼저 탄다
    "PROBLEM_TITLE",                # ③ 폴백
    "UNAVAILABLE",                  # ④ NOT_GENERATED -- title조차 CHECK로 NULL이라 개념명이 없다
]
ValidityReviewStatus = Literal["NOT_REQUIRED", "PENDING", "CONFIRMED_INVALID", "RESTORED_VALID"]

# 2026-08-07 백엔드 최종 회신에서 정책 담당자 확정 + DB CHECK 적용 완료. 그전까지는
# "Literal 걸지 마십시오"였다(§1-7). 임시 Postgres 17로 값·짝 검증까지 마쳤다고 확인됨.
ValidityTriggerReasonCode = Literal[
    "EXCESSIVE_WINDOW_LEAVE",     # 창 이탈 임계치 초과
    "EXCESSIVE_CONNECTION_LOSS",  # 연결 끊김 임계치 초과
    "MANAGER_MANUAL_FLAG",        # 자동 신호 없이 매니저가 직접 검토 개시
]
# 🔴 decision 값과 status의 짝이 DB CHECK로 강제된다 -- 어긋난 조합은 저장 자체가 거부된다.
#   REVIEWED_NO_VIOLATION          -> RESTORED_VALID
#   REVIEWED_VIOLATION_CONFIRMED   -> CONFIRMED_INVALID
#   REVIEWED_INSUFFICIENT_EVIDENCE -> RESTORED_VALID   (증거 불충분은 유효 처리다)
# 프롬프트가 이 대응을 전제로 써도 된다는 뜻이다.
ValidityDecisionReasonCode = Literal[
    "REVIEWED_NO_VIOLATION",
    "REVIEWED_VIOLATION_CONFIRMED",
    "REVIEWED_INSUFFICIENT_EVIDENCE",
]
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
# D-ib4 (2026-08-06): 백엔드가 실제 DDL(테이블정의서_v07_교육생홈_DDL.sql,
# ck_assessment_session_end_reason_code)로 감사한 결과 원래 9종이 아니라 13종이었다
# -- 뒤 4개(REVIEW_DUE_AT_EXPIRED 등)가 오면 여기 Literal이 422로 거부하고 있었다.
# DDL 원문에서 직접 대조한 값이라 추측이 아니다.
SessionEndReasonCode = Literal[
    "ALL_PROBLEMS_TERMINAL", "ALL_REVIEW_TARGETS_TERMINAL", "COMPLETED_L4",
    "TERMINATED_AT_L1", "TERMINATED_AT_L2", "TERMINATED_AT_L3", "TERMINATED_AT_L4",
    "POLICY_TIME_LIMIT_EXCEEDED", "ASSESSMENT_WINDOW_EXPIRED",
    "REVIEW_DUE_AT_EXPIRED", "DATA_INTEGRITY_INVALID", "ADMIN_INVALIDATED",
    "TECHNICAL_FAILURE",
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
    # 무효 확인이 "왜 시작됐는가"(trigger)와 "그래서 어떻게 판정했는가"(decision)다.
    # 2026-08-07에 값 집합이 확정돼 Literal로 좁혔다(그전엔 CHECK가 없어 str로 열어 뒀다).
    trigger_reason_code: ValidityTriggerReasonCode | None = Field(
        default=None,
        description="무효 확인이 시작된 사유. briefType=INVALID_ATTEMPT일 때 특히 중요한 근거",
    )
    # ⚠️ status가 PENDING·NOT_REQUIRED면 이 값이 오지 않는다(DB CHECK
    # ck_measurement_attempt_validity_review_status_2). 판정 전이라 판정 사유가 없다.
    decision_reason_code: ValidityDecisionReasonCode | None = None
    decision_note: str | None = Field(
        default=None, description="매니저가 남긴 판정 메모",
    )


class ComprehensionCodeContext(BaseSchema):
    """문제 근거 코드의 **좌표와 식별자만**. 원문은 전달하지 않는다.

    백엔드 회신 §3 A-2가 6필드를 제안했고 그대로 받는다. 원문을 안 보내는 이유가
    바뀌었다 -- 예전엔 명세 §4.1이 그렇게 적어서였고, 지금은 `assessment_problem`에
    코드 원문 자체가 없기 때문이다(좌표와 키만 있고 원문은 `submission.code_snippets`
    JSONB에 있다). 게다가 GitHub 접근 주체가 이미 AI 서버라(S-01/S-10) 백엔드가
    조립해 되돌려주는 건 중복이면서 코드 외부 전송 범위만 넓힌다.

    ⚠️ `generation_status='NOT_GENERATED'` 문제는 이 6개가 DB CHECK로 전부 NULL이라
    **객체 자체가 생략된다**(`ProblemComprehension.code_context`가 optional인 이유).
    """

    language: str
    path: str
    line_start: int
    line_end: int
    snippet_key: str = Field(
        description="`submission.code_snippets`(JSONB, 최대 3원소) 안에서 이 근거가 "
                    "가리키는 원소를 찾는 키(= `assessment_problem.source_snippet_key`)",
    )
    snippet_hash: str = Field(
        description="원문 무결성 확인용 해시(= `assessment_problem.code_snippet_hash`). "
                    "AI가 저장소에서 직접 가져온 내용과 대조하는 용도이고 그 자체를 "
                    "화면에 표시하지 않는다",
    )


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
    concept_name: str | None = Field(
        default=None,
        description="검증 개념 표시명. ★ 질문에서 L2 같은 내부 코드 대신 이 이름을 쓴다. "
                    "🔴 conceptNameSource=UNAVAILABLE이면 **null이다** -- DB 컬럼이 아니라 "
                    "매번 조인으로 계산되는 값인데, NOT_GENERATED 문제는 CHECK가 title까지 "
                    "NULL로 강제해 폴백 체인이 전부 비기 때문이다(2026-08-07 확정)",
    )
    problem_scope: ProblemScope
    generation_status: GenerationStatus
    not_generated_reason_code: NotGeneratedReasonCode | None = None
    best_success_stage: BestSuccessStage | None = None
    code_context: ComprehensionCodeContext | None = Field(
        default=None, description="NOT_GENERATED 문제는 없다",
    )
    # concept_name은 problem_scope에 따라 조인 경로가 갈린다(백엔드 회신 §3 D-2 +
    # 부록 A). 그 경로를 AI가 알아야 확신도를 조절할 수 있어서 백엔드가 같이 보낸다.
    # 🔴 옛 선택 필드(3종 추측값)에서 **필수 + 4종**으로 바뀌었다 -- 부록 A의 CASE에
    # ELSE가 있어 문제 하나는 반드시 넷 중 하나로 귀결된다.
    #
    # ⚠️ `UNAVAILABLE`일 때 `concept_name`에 무엇이 오는지는 아직 열린 질문이다.
    # 부록 A의 COALESCE 체인이 전부 NULL이면 `concept_name`도 NULL인데 이 스키마는
    # required str이다 -- 빈 문자열로 올지 필드가 빠질지 백엔드 회신 대기 중이다.
    concept_name_source: ConceptNameSource = Field(
        description="conceptName이 어느 경로에서 나왔는지(ConceptNameSource 4종). "
                    "TEACHES_CANONICAL_NAME 외에는 검증된 개념명이 아니므로 "
                    "단정적으로 서술하지 않는다",
    )
    # D-ib4 (백엔드 D-1 대응): NOT_GENERATED 문제는 stages가 비어 이 문제를 근거로
    # 삼을 interviewSourceId가 없었다(interview_source 테이블의 problem_id 슬롯이
    # DDL엔 있는데 요청이 안 실었다는 게 백엔드 진단). ComprehensionStage.
    # interview_source_id와 같은 패턴 -- 문제 하나당 자기 근거 ID 하나.
    interview_source_id: str = Field(
        description="★ 이 문제를 (단계 단위가 아니라 문제 단위로) 근거로 질문을 "
                    "만들면 이 값을 그대로 실어야 한다. NOT_GENERATED처럼 stages가 "
                    "빈 문제에서 특히 필요하다",
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
    # D-ib4 (백엔드 D-1 대응): NOT_ATTENDED면 problems가 통째로 빈 배열이라(위 필드
    # description 참고) 미응시 질문에 실을 interviewSourceId가 아예 없었다 --
    # interview_source의 attempt_id 슬롯을 이 값으로 채운다. session_id는 세션
    # 자체가 안 열렸을 수 있어(NOT_ATTENDED) 선택으로 둔다.
    attempt_interview_source_id: str = Field(
        description="★ 이 시도 전체(예: 미응시 사실 자체)를 근거로 질문을 만들면 "
                    "이 값을 그대로 실어야 한다",
    )
    session_interview_source_id: str | None = Field(
        default=None,
        description="★ sessionEndReasonCode를 근거로 질문을 만들면 이 값을 그대로 "
                    "실어야 한다. 세션이 열리지 않았으면(NOT_ATTENDED 등) 없다",
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
    # D-ib4 (백엔드 D-1 대응): 원래 이 클래스엔 id가 전혀 없어서, 관찰 메모만 근거인
    # 라포 질문은 interviewSourceId를 정직하게 null로 둘 수밖에 없었다(D-ib3).
    # observation_note 테이블 코멘트 자체가 "InterviewSource.observation_note_id로
    # 면담 근거에 선택 연결한다"고 이미 이 경로를 전제하고 있어, 요청에 이 값만
    # 추가하면 null 빈도를 줄일 수 있다는 게 백엔드 진단.
    interview_source_id: str = Field(
        description="★ 이 관찰 메모를 근거로 질문을 만들면 이 값을 그대로 실어야 한다",
    )
    # 값 집합이 확정됐다(백엔드 회신 §1-5, 2026-08-07). DDL에 CHECK가 없던 건
    # "정책 확정 전 임의로 고정하지 않는다"는 이유였고 이제 시드 실사용값으로 닫혔다.
    # ⚠️ 옛 계획("값 집합이 정해지면 공개범위별 필터링을 붙인다")은 **철회됐다**(§3 A-6).
    visibility: ObservationNoteVisibility = Field(
        description="공개범위 코드. 백엔드가 요청 시점에 이미 걸러 보내므로 AI는 "
                    "필터링에 쓰지 않는다(§3 A-6) -- 프롬프트 톤 조절용 참고값이다",
    )


class InterviewBriefRequest(BaseSchema):
    """POST /api/v0/interview-briefs 요청 본문 전체."""

    brief_id: UuidStr = Field(
        description="`interview_brief.brief_id`. AI는 이 값을 그대로 `aiUsage.contextId`에 "
                    "에코한다(`contextType='INTERVIEW_BRIEF'`일 때 고정 대응). "
                    "🔴 백엔드가 **AI 호출 전에** 브리프 행을 만들고 그 id를 실어 보낸다 "
                    "(2026-08-07 확정) -- `last_request_id`/`last_request_fingerprint` 쌍이 "
                    "중복 호출 방지 장치라, 호출 후에 행을 만들면 대조할 대상이 없어 "
                    "장치가 무의미해진다",
    )
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
    interview_source_id: str = Field(
        description="요청에서 받은 값 중 하나여야 한다 -- 새 UUID면 백엔드가 저장을 거부한다. "
                    "🔴 null이 될 수 없다: interview_brief_item.interview_source_id가 "
                    "UUID NOT NULL이라 null인 항목은 그 행 하나가 통째로 저장 불가다. "
                    "id 없는 근거(priorInterviews·briefContext)만으로 만든 라포 질문은 "
                    "시도 단위 id(comprehension.attemptInterviewSourceId)로 떨어진다",
    )


class InterviewBriefResponse(BaseSchema):
    """성공 응답 본문. §5. jobId 없음 -- 이 응답이 곧 결과다.

    🔴 옛 전용 `UsageMeta`는 삭제됐다(2026-08-07). 다른 네 엔드포인트와 같은 공용
    `AiUsage`를 쓴다 -- UsageMeta에는 featureCode·contextType·requestId·traceId·
    idempotencyKey가 없어서 백엔드가 ai_usage 행을 만들려면 전부 스스로 합성해야 했다.
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
    ai_usage: list[AiUsage] = Field(
        default_factory=list,
        description="이 요청이 태운 LLM 호출 기록. 브리프는 호출 1회라 보통 1행이다",
    )


class InterviewBriefFailure(BaseSchema):
    """503 실패 응답 본문. §5.2 -- 공용 `{error, message, retryable}`과 계약이 다르다.

    `aiUsage`가 성공 응답과 같은 자리에 있다(백엔드 회신 §3 A-5: 성공·실패 **모든**
    봉투에 사용량을 싣는다). LLM을 부르기도 전에 실패했으면 빈 배열이다.
    """

    failure_code: str = Field(
        description="명세 §5.2의 8종 중 하나. 전송 계층 실패(TIMEOUT·RATE_LIMITED·"
                    "PROVIDER_ERROR·CONTEXT_OVERFLOW)는 그대로 전달하고, 모델이 계약을 "
                    "어긴 경우(개수·순서·지어낸 interviewSourceId)는 INVALID_JSON이다",
    )
    message: str
    ai_usage: list[AiUsage] = Field(default_factory=list)
