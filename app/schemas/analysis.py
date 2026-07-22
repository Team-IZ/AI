""" 코드 분석 API(P02)의 요청 응답 스키마"""

from typing import Literal

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