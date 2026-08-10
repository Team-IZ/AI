""" 교안 분석 API의 요청 응답 스키마.

DB 3계층(curriculum_analysis → curriculum_section → teaches)을 그대로 따른다.
AI는 UUID를 만들지 않는다 — 구조만 돌려주고 Spring이 INSERT하며 키를 발급한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema, UuidStr
from app.schemas.usage import AiUsage


class CurriculumRequest(BaseSchema):
    """POST /api/v0/curricula 요청. PDF는 multipart의 file 파트로 온다."""

    version_id: UuidStr = Field(
        description="분석 대상 교안 **버전**. `curriculum_version.version_id`다. "
                    "⚠️ `/analyses`에는 대응 필드가 없다 — 거기 있던 "
                    "`curriculumVersionId`는 2026-08-10에 삭제했다(한 프로젝트에 교안 "
                    "버전이 여러 개라 단일 값으로 표현이 안 되고, AI가 읽지도 않았다). "
                    "분석 쪽 교안 출처는 `teaches[]`가 개념 단위로 나른다",
    )
    curriculum_id: UuidStr | None = Field(
        default=None,
        description="교안 자산 `curriculum_material.material_id`. **버전이 아니다** — "
                    "버전은 위 `versionId`가 가리킨다",
    )
    course_label: str = Field(
        min_length=1, max_length=80,
        description="🔴 **필수다.** 과정명(예: 'SQL', 'Java', 'AI Agent'). 프롬프트 "
                    "프레이밍에 쓴다 — 없으면 매니페스트 기본값('Java')이 들어가고, "
                    "다른 과정 교안이면 모델이 흔들려 결과 언어·용어가 섞인다. "
                    "**교안 업로드 화면이 과정을 이미 알고 있으므로 기본값을 두지 않는다** "
                    "(2026-08-03 확정)",
    )
    provider_model_code: str | None = Field(
        default=None,
        description="공급자에게 그대로 넘길 모델 식별자. 값은 `ai_model.provider_model_code`"
                    "(벤더 접두어 포함). 생략 시 서버 기본값. operator가 고른다",
    )
    # callbackUrl은 없다 (2026-08-03 확정, PLAN §T11 D-3). 202 + 폴링으로 간다.


class CurriculumAccepted(BaseSchema):
    """202 응답. 결과는 폴링으로 가져간다."""

    job_id: str
    status: Literal["QUEUED"]


# 교안 항목의 성격. p01-2가 이미 이 값으로 답한다 — 예전엔 받고도 버렸다.
#
# 🔴 **문제 선정의 재료다** (PM 설계 v2 §7). 개념이 코드 어디 있는지 찾을 때
#   CODE_EXAMPLE  식별자 추출원. `st.title`·`function_tool` 같은 이름이 여기서 나온다
#   CAUTION       "언제 깨지는가"(L4)의 재료이자 선별 순서 신호
#   CONCEPT       개념 정의. L1 재료
TeachKind = Literal["CONCEPT", "CODE_EXAMPLE", "CAUTION"]


class Teach(BaseSchema):
    """교안에서 뽑은 학습 개념 하나. **두 테이블에 나뉘어 들어간다.**

        teaches                     canonicalName · normalizedName · canonicalDescription
        curriculum_teaches_mapping  extractedName(=canonicalName) · descriptionPage* ·
                                    confidence · kind · evidence · siblingNames

    ⚠️ **AI는 기관 표준명을 정할 수 없다.** 교안 한 권만 보기 때문이다 — 여기서 나오는
    `canonicalName`은 "이 교안에서 실제로 쓰인 표현"이고 그게 곧
    `curriculum_teaches_mapping.extracted_name`이다. `teaches.canonical_name`은
    **개념을 새로 만들 때만** 같은 값으로 시작하고, 이후 기존 개념과 묶을지(MERGED)
    표준명을 뭘로 쓸지는 기관 원장을 가진 Spring이 정한다.
    """

    canonical_name: str = Field(
        max_length=200,
        description="이 교안에서 실제로 쓰인 개념 표현. "
                    "`curriculum_teaches_mapping.extracted_name`에 그대로 넣으면 되고, "
                    "`teaches.canonical_name`은 신규 개념일 때만 같은 값으로 시작한다",
    )
    normalized_name: str = Field(
        max_length=200,
        description="중복 판정용 정규화 이름. ⚠️ AI는 공백·대소문자만 정리한다 — "
                    "활성 개념 부분 UNIQUE의 판정 주체가 Spring이므로 "
                    "**기관 규칙(NFKC 등)으로 다시 정규화하는 쪽이 안전하다**",
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="DB `curriculum_teaches_mapping.confidence`(NOT NULL, 0~1). "
                    "구간 confidence와 같은 의미의 거친 등급이다 — 확률이 아니다",
    )
    canonical_description: str | None = Field(
        default=None, description="설명을 못 찾으면 null"
    )
    description_page_start: int | None = Field(default=None, ge=1)
    description_page_end: int | None = Field(default=None, ge=1)

    # ── 아래 3개는 2026-08-04 추가. **LLM 호출이 늘지 않는다** ──────────────
    # p01-2가 이미 `kind`·`evidence`를 답에 담아 보내는데 우리가 버리고 있었다.
    # `siblings`는 같은 unit의 다른 개념이라 계산만 하면 된다.
    kind: TeachKind = Field(
        default="CONCEPT",
        description="교안 항목의 성격. **화면은 CONCEPT만 보여주면 된다** — "
                    "나머지 둘은 문제 선정 재료라 저장만 하면 됩니다",
    )
    evidence: str | None = Field(
        default=None,
        description="이 개념이 나온 페이지 근거를 모델이 짧게 옮긴 것. "
                    "코드 식별자가 여기 들어 있는 경우가 많아 사전 추출원으로 쓴다",
    )
    sibling_names: list[str] = Field(
        default_factory=list,
        description="같은 unit의 다른 개념 이름들. **교안이 대안을 가르쳤다는 신호**이고 "
                    "L3(대안 비교) 질문의 재료다. `normalizedName` 기준",
    )

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

    module_no: int = Field(
        ge=1,
        description="교안 안에서의 모듈 순서. DB `curriculum_section.sequence_no`다 "
                    "(이름만 다르다). 1부터 빈틈없이 증가하고 UNIQUE(분석, 순번)를 만족한다",
    )
    title: str = Field(max_length=200)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    keywords: list[str] = Field(
        default_factory=list,
        description="DB `curriculum_section.keywords`(JSONB, NOT NULL). "
                    "이 구간에서 뽑힌 개념 이름들이다 — 별도 LLM 호출로 만든 값이 아니라 "
                    "`teaches[].canonicalName`을 모아 둔 것이다. 검색·목록 미리보기용",
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="DB `curriculum_section.confidence`(NOT NULL, 0~1). "
                    "⚠️ **확률이 아니다.** 모델은 신뢰도를 내지 않는다 — 두 등급뿐이다: "
                    "1.0=정상 추출, 0.5=이 구간의 페이지에 걸친 청크가 실패해 개념이 "
                    "빠졌을 수 있음. `fallbackUsed`가 교안 전체 단위인 데 비해 이 값은 "
                    "**어느 구간이 부실한지**를 가리킨다. 정렬·경고 표시에만 쓰세요",
    )
    teaches: list[Teach] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_pages(self) -> "CurriculumSection":
        """DB CHECK: page_end >= page_start. UNIQUE(version_id, module_no)는 Spring이 본다."""
        if self.page_end < self.page_start:
            raise ValueError("pageEnd는 pageStart보다 작을 수 없습니다")
        return self


class CurriculumResult(BaseSchema):
    """교안 분석이 끝났을 때의 결과 본문. DB curriculum_analysis 대응."""

    version_id: UuidStr
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
    version_id: UuidStr | None = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
    failure_reason: str | None = Field(default=None, description="FAILED일 때만 채워진다")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: CurriculumResult | None = Field(default=None, description="SUCCEEDED·PARTIAL일 때만")
    ai_usage: list[AiUsage] = Field(default_factory=list)