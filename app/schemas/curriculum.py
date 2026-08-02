""" 교안 분석 API의 요청 응답 스키마.

DB 3계층(curriculum_analysis → curriculum_section → teaches)을 그대로 따른다.
AI는 UUID를 만들지 않는다 — 구조만 돌려주고 Spring이 INSERT하며 키를 발급한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema
from app.schemas.usage import AiUsage


class CurriculumRequest(BaseSchema):
    """POST /api/v0/curricula 요청. PDF는 multipart의 file 파트로 온다."""

    version_id: str = Field(description="Spring curriculum_version 키(에코용)")
    curriculum_id: str | None = None
    course_label: str | None = Field(
        default=None, max_length=80,
        description="과정명(예: 'SQL', 'Java'). 프롬프트 프레이밍에 쓴다 — 생략하면 "
                    "매니페스트 기본값('Java')이 들어가고, 다른 과정 교안이면 "
                    "모델이 흔들려 결과 언어·용어가 섞인다",
    )
    model_code: str | None = Field(
        default=None, description="생략 시 서버 기본값. operator가 고른다"
    )
    callback_url: str | None = Field(
        default=None, description="완료 통지 수신 주소. 현재는 수용만 하고 전송은 미구현"
    )


class CurriculumAccepted(BaseSchema):
    """202 응답. 결과는 폴링으로 가져간다."""

    job_id: str
    status: Literal["QUEUED"]


class Teach(BaseSchema):
    """교안에서 뽑은 학습 개념 하나. DB teaches 대응."""

    canonical_name: str = Field(max_length=200, description="표준 개념명")
    normalized_name: str = Field(max_length=200, description="중복 판정용 정규화 이름")
    canonical_description: str | None = Field(
        default=None, description="설명을 못 찾으면 null"
    )
    description_page_start: int | None = Field(default=None, ge=1)
    description_page_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_pages(self) -> "Teach":
        """DB CHECK: description_page_end >= description_page_start."""
        if (
            self.description_page_start is not None
            and self.description_page_end is not None
            and self.description_page_end < self.description_page_start
        ):
            raise ValueError("descriptionPageEnd는 descriptionPageStart보다 작을 수 없습니다")
        return self


class CurriculumSection(BaseSchema):
    """교안의 모듈 하나. DB curriculum_section 대응."""

    module_no: int = Field(ge=1, description="교안 안에서의 모듈 순서")
    title: str = Field(max_length=200)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    teaches: list[Teach] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_pages(self) -> "CurriculumSection":
        """DB CHECK: page_end >= page_start. UNIQUE(version_id, module_no)는 Spring이 본다."""
        if self.page_end < self.page_start:
            raise ValueError("pageEnd는 pageStart보다 작을 수 없습니다")
        return self


class CurriculumResult(BaseSchema):
    """교안 분석이 끝났을 때의 결과 본문. DB curriculum_analysis 대응."""

    version_id: str
    analysis_version: int = Field(ge=1, description="분석 파이프라인 버전. 재현성 근거")
    heuristic_version: int | None = None
    prompt_version: int | None = None
    # CHECK가 없는 확장형 업무 코드다. 값 카탈로그는 백엔드 확인 대기(C-5).
    extraction_status: str = Field(description="추출 상태 코드")
    quality_status: str | None = Field(
        default=None, description="분석 완료 전에는 확정되지 않을 수 있다"
    )
    fallback_used: bool = Field(
        default=False, description="LLM 실패로 룰 기반 추출로 물러났는지"
    )
    sections: list[CurriculumSection] = Field(default_factory=list)


class CurriculumJobStatus(BaseSchema):
    """GET /curricula/{jobId} 응답. analysis_job과 같은 상태 어휘를 쓴다."""

    job_id: str
    version_id: str | None = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
    failure_reason: str | None = Field(default=None, description="FAILED일 때만 채워진다")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: CurriculumResult | None = Field(default=None, description="SUCCEEDED·PARTIAL일 때만")
    ai_usage: list[AiUsage] = Field(default_factory=list)