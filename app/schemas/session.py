""" 문답 세션 API(P03)의 요청 응답 스키마 """
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import BaseSchema
from app.schemas.report import AxisCode  # 축 = 문답 레벨. 정의는 report.py 한 곳뿐

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
    """확정된 문답 한 턴. Spring이 즉시 영속화하는 복구 근거."""
    problem_id: str
    axis_code: AxisCode
    question_text: str
    answer_text: str
    answered_at: str
    
class SessionView(BaseSchema):
    """세션 응답.

    명세는 진행/종료 두 형태로 나뉘지만, 스텁에서는 한 모델에 선택 필드로 담아
    단순하게 간다(진행 중이면 current+progress, 끝났으면 transcript).
    실제 계약을 굳힐 때 분리 여부를 정한다.
    """
    session_id: str
    state: Literal["IN_PROGRESS", "COMPLETED", "TIMEOUT", "FAILED"]
    current: Question | None = None
    progress: Progress | None = None
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    ai_usage: list[dict[str, Any]] = Field(default_factory=list)
    
class SessionRestore(BaseSchema):
    """POST /sessions/{id}/restore 요청 (명세 §4.4)."""
    attempt_id: str | None = None
    analysis_job_id: str | None = None
    time_limit_sec: int = 2400
    elapsed_sec: int = 0
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    problems: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any] | None = None