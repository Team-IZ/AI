""" 5축 후채점 API 요청/응답 스키마. 표기는 camelCase """
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import BaseSchema

# 5축 코드, 축별 1~5점, 총점 5~25점, 동일가중
AxisCode = Literal[
    "CODE_UNDERSTANDING",
    "DESIGN_LOGIC",
    "ALTERNATIVE_COMPARISION",
    "COUNTEREXAMPLE_RESPONSE",
    "SELF_CORRECTION",
]

class GradingRequest(BaseSchema):
    """ POST /gradings 요청. 세션 종료 후 transcript 전체를 채점 """
    session_id: str | None = None
    score_run_id: str | None = Field(default=None, description="Spring ScoreRun 키(에코용)")
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    
class AxisEvidence(BaseSchema):
    """ 점수 근거. 답변 인용 필수 """
    turn_ref: int
    quote_text: str
    reason: str
    
class AxisEvidence(BaseSchema):
    """ 점수 근거. 답변 인용 필수 """
    turn_ref: int
    quote_text: str
    reason: str
    
class AxisScore(BaseSchema):
    axis_code: AxisCode
    score: int = Field(ge=1, le=5)
    evidence: list[AxisEvidence] = Field(default_factory=list)
    
class GradingVersions(BaseSchema):
    """ 어떤 모델,프롬프트,루브릭으로 채점했는지. """
    model_code: str
    prompt_version: str
    rubric_version: str
    
class GradingResult(BaseSchema):
    axis_scores: list[AxisScore]    # 정확히 5축으로
    total_score: int                # 5~25
    average_score: float
    versions: GradingVersions
    
class GradingAccepted(BaseSchema):
    """ 202 응답 """
    job_id: str
    status: Literal["QUEUED"]
    
class GradingJobStatus(BaseSchema):
    """ GET /gradings/{jobId} 응답
    
    status 분석 job과 다름: RUNNING 아니라 GRADING
    PARTIAL =  일부 축만 실패
    """
    job_id: str
    session_id: str | None = None
    status: Literal["QUEUED", "GRADING", "COMPELETED", "PARTIAL", "FAILED"]
    failure_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: GradingResult | None = None
    ai_usage: list[dict[str, Any]] = Field(default_factory=list)