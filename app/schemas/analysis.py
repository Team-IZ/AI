""" 코드 분석 API(P02)의 요청 응답 스키마"""
from datetime import datetime
from typing import Any, Literal
from app.schemas.report import AxisCode

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema

class AnalysisSource(BaseSchema):
    repo_url: str | None = Field(
        default=None, description="method=GITHUB_URL일 때 필수. 공개 레포만 지원"
    )
    branch: str | None = Field(default=None, description="생략 시 기본 브랜치")
    
class AnalysisRequest(BaseSchema):
    """ POST /api/v0/analyses 요청 본문 """
    
    attempt_id: str | None = Field(default=None, description="Spring 측 측정수행 키(에코용)")
    submission_id: str | None = None
    callback_url: str | None = Field(
        default=None, description="완료 통지 수신 주소. 현재는 수용만 하고 전송은 미구현"
    )
    method: Literal["GITHUB_URL", "ZIP_WITH_GITLOG"]
    source: AnalysisSource = Field(default_factory=AnalysisSource)
    extraction_scope: Literal["TOTAL", "OWN_COMMIT"] = "TOTAL"
    commit_email: str | None = Field(default=None, description="OWN_COMMIT일 때 필수")
    question_budget: int = Field(default=4, ge=1, description="계획 질문 수")
    focus_areas: list[str] = Field(default_factory=list)
    
    @model_validator(mode="after")
    def _check_conditional_fields(self) -> "AnalysisRequest":
        """ 다른 필드 값에 따라 필수가 되는 것들을 검사 
        
        mode="after"는 개별 필드 검증 끝난 후 실행하라는 뜻
        """
        
        if self.method == "GITHUB_URL" and not (self.source.repo_url or "").strip():
            raise ValueError("method=GITHUB_URL에는 source.repoUrl이 필요합니다")
        if self.extraction_scope == "OWN_COMMIT" and not (self.commit_email or "").strip():
            raise ValueError("extractionScope=OWN_COMMIT에는 commitEmail이 필요합니다")
        return self

class AnalysisAccepted(BaseSchema):
    """ 202 응답. 점수만 알리고 결과는 폴링으로 가져감 """
    
    job_id: str
    status: Literal["QUEUED"]
    
class SnapshotMeta(BaseSchema):
    """ code_snapshot 테이블 대응. 코드 원문 저장하지 않고 메타만 주기 """
    
    content_hash: str = Field(description="sha256 hex 64자")
    file_count: int
    byte_count: int

class ProblemReference(BaseSchema):
    """문제가 가리키는 코드 위치. DB problem_reference 대응."""

    path: str
    line_start: int
    line_end: int
    evidence_hash: str = Field(description="sha256 hex 64자")
    reference_type: str = Field(description="PRIMARY 등. 카탈로그 미정(B-3)")
    
class Hint(BaseSchema):
    """단계 하나에 딸린 힌트. 분석 때 미리 만들어 동결한다."""

    hint_level: int = Field(ge=1, le=2)
    hint_text: str
    
class ProblemStage(BaseSchema):
    """문제 하나의 레벨 하나. 4축이 곧 4단계다."""

    axis_code: AxisCode
    question_text: str
    flagged: bool = Field(
        default=False,
        description="보기형(①②③ 등)이 섞여 재생성에도 실패한 질문. 화면에 '검수 필요'",
    )
    hints: list[Hint]

class Problem(BaseSchema):
    """출제 대상 코드 지점. DB assessment_problem 대응.

    후보와 채택본을 status로 구분한다. 별도 타입을 두지 않는다.
    """

    problem_id: str
    status: Literal["CANDIDATE", "READY", "USED", "SKIPPED", "INVALID"]
    priority: float
    focus_code: str
    source_path: str
    line_start: int
    line_end: int
    evidence_hash: str
    references: list[ProblemReference] = Field(default_factory=list)
    stages: list[ProblemStage] = Field(default_factory=list)
    
class AnalysisResult(BaseSchema):
    """분석이 성공했을 때의 결과 본문.

    problems 각 항목의 내부 구조는 팀원 엔진 결과에 따라 바뀌므로
    dict로 열어둔다(PLAN §3 C7). 최상위 필드만 계약으로 고정한다.
    """

    snapshot_id: str = Field(description="Spring code_snapshot 행의 키")
    snapshot_meta: SnapshotMeta
    applied_scope: Literal["TOTAL", "OWN_COMMIT"]
    scope_fallback: bool = Field(description="요청 범위를 못 지켜 TOTAL로 물러났는지")
    fallback_reason: str | None = None
    commit_sha: str | None = None
    problems: list[dict[str, Any]] = Field(default_factory=list)
    question_count_planned: int = Field(description="계획된 질문 수. 유효 문제가 적으면 축소된다")
    
class AnalysisJobStatus(BaseSchema):
    """GET /analyses/{jobId} 응답.

    status 값은 DB analysis_job.status의 CHECK 제약과 같다(PLAN §3).
    ANALYZING·READY는 다른 테이블의 값이라 여기 쓰면 Spring INSERT가 깨진다.
    """

    job_id: str
    attempt_id: str | None = None
    submission_id: str | None = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
    failure_reason: str | None = Field(default=None, description="FAILED일 때만 채워진다")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: AnalysisResult | None = Field(default=None, description="SUCCEEDED·PARTIAL일 때만")
    # 스텁 단계에서는 항상 빈 배열이다. P02가 LLM 파이프라인으로 교체되는 중이라
    # (2026-07-29, PLAN §4) 실물 엔진이 붙으면 호출 기록이 채워진다.
    ai_usage: list[dict[str, Any]] = Field(default_factory=list)