""" 문답 세션 API(P03)의 요청 응답 스키마 """
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import BaseSchema
from app.schemas.report import AutonomyCode, AxisCode  # 축 = 문답 레벨. 정의는 report.py 한 곳뿐
from app.schemas.usage import AiUsage
from app.schemas.analysis import Problem

class CodeContext(BaseSchema):
    """ 질문이 가르키는 코드 발췌 """
    path: str
    snippet: str
    line_start: int
    
class Question(BaseSchema):
    """ 지금 물어보는 질문 하나 """
    problem_id: str               # DB assessment_problem 키
    axis_code: AxisCode           # 문답 깊이(축) L1~L4
    sequence_no: int
    question_text: str
    code_context: CodeContext | None = None

class Progress(BaseSchema):
    problem_index: int            # 몇 번째 문제인지(1부터)
    problem_total: int

class SessionStart(BaseSchema):
    """POST /sessions 요청."""
    attempt_id: str | None = None
    analysis_job_id: str | None = None
    session_id: str | None = Field(default=None, description="Spring AssessmentSession 키(에코용)")
    selected_problem_ids: list[str] = Field(default_factory=list, description="세션에 포함할 문제. 생략 시 전체")
    time_limit_sec: int = Field(default=2400, ge=1)
    
class AnswerSubmit(BaseSchema):
    """POST /sessions/{id}/answers 요청."""
    client_request_id: str = Field(description="세션 내 유일 멱등키")
    answer_text: str
    
class TranscriptTurn(BaseSchema):
    """확정된 문답 한 턴. Spring이 즉시 영속화하는 복구 근거.

    한 턴 = 질문 1개 + 답변 1개 + 채점 1개. 힌트 후 재질의는 별도 턴이 되고
    attempt_no가 올라간다(DB stage_answer_attempt 한 행에 대응).
    """
    problem_id: str
    axis_code: AxisCode
    question_text: str
    answer_text: str
    answered_at: str

    # 채점 결과. 단계마다 즉시 매겨진다.
    best_score: int = Field(ge=0, le=5, description="힌트 상한 적용 전 원점수")
    confirmed_score: int = Field(ge=0, le=5, description="힌트 상한 적용 후 기록 점수")
    attempt_count: int = Field(
        ge=1, le=3, description="이 단계에서 몇 번째 시도인지. 첫 답변이 1"
    )
    hint_text: str | None = Field(
        default=None, description="이 턴 직전에 보여준 힌트. 첫 시도면 null"
    )
    autonomy: AutonomyCode | None = None
    
class SessionView(BaseSchema):
    """세션 응답.

    명세는 진행/종료 두 형태로 나뉘지만, 스텁에서는 한 모델에 선택 필드로 담아
    단순하게 간다(진행 중이면 current+progress, 끝났으면 transcript).
    실제 계약을 굳힐 때 분리 여부를 정한다.
    """
    session_id: str
    # DB assessment_session.status CHECK와 같은 집합.
    # READY = 분석 직후 미리 만들어 둔 상태(문제보다 세션이 먼저 있어야 한다).
    # TIMEOUT은 DB에 없다 — 시간 초과는 EXPIRED다.
    state: Literal[
        "READY", "IN_PROGRESS", "PAUSED", "COMPLETED", "FAILED", "EXPIRED"
    ]
    current: Question | None = None
    progress: Progress | None = None
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    ai_usage: list[AiUsage] = Field(default_factory=list)
    
class SessionRestore(BaseSchema):
    """POST /sessions/{id}/restore 요청 (명세 §4.4)."""
    attempt_id: str | None = None
    analysis_job_id: str | None = None
    time_limit_sec: int = 2400
    elapsed_sec: int = 0
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
    source: dict[str, Any] | None = None