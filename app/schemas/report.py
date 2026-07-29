""" 보고서 API의 요청/응답 스키마. 표기는 camelCase

이전 grading.py(세션 종료 후 5축 후채점)를 대체한다. P04에서 채점이 문답 도중
레벨마다 일어나므로 세션이 끝난 시점에 따로 채점할 것이 없다. 남는 비동기 작업은
보고서 생성(LLM + 교안 참조 조회)뿐이라 리소스 이름을 그것에 맞췄다.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import BaseSchema

# 4축. 축이 곧 문답 레벨이다(L1 통과해야 L2로 간다).
# 점수는 0~5. 0은 무응답이거나 질문과 무관한 답.
AxisCode = Literal[
    "L1_CODE_DESCRIPTION",     # 코드 기술 — 무엇을 어떻게 구현했는가
    "L2_DESIGN_LOGIC",         # 설계 논리 — 왜 이렇게 설계했는가
    "L3_COUNTEREXAMPLE",       # 반례·한계 — 이 설계가 깨지는 조건은
    "L4_ALTERNATIVE",          # 대안 — 다른 선택지와 비교해 왜 이것인가
]

# 힌트 사용 횟수에서 파생된다. 0회=SELF, 1회=SELF_MAINTAINED, 2회=PARTIAL.
AutonomyCode = Literal["SELF", "SELF_MAINTAINED", "PARTIAL"]


class ReportRequest(BaseSchema):
    """POST /reports 요청. 세션이 끝난 뒤 전사와 재료를 함께 넘긴다."""

    session_id: str | None = None
    score_run_id: str | None = Field(default=None, description="Spring ScoreRun 키(에코용)")
    transcript: list[dict[str, Any]] = Field(
        default_factory=list, description="점수가 이미 확정된 턴 기록"
    )
    analysis_documents: list[dict[str, Any]] = Field(
        default_factory=list, description="[{kind, content}] 코드 분석 문서"
    )
    teaches: list[dict[str, Any]] = Field(
        default_factory=list, description="[{id, label, unitId, sourcePages}] 교안 참조용"
    )


class LevelScore(BaseSchema):
    """문제 하나의 레벨 하나. 도달하지 못한 레벨은 reached=false에 점수가 없다."""

    axis_code: AxisCode
    reached: bool = Field(description="이 레벨까지 진행했는지. false면 앞 레벨에서 끝났다")
    raw_score: int | None = Field(default=None, ge=0, le=5, description="LLM 원점수")
    score: int | None = Field(
        default=None, ge=0, le=5, description="힌트 상한을 적용한 기록 점수"
    )
    hints_used: int = Field(default=0, ge=0, le=2)
    autonomy: AutonomyCode | None = None


class QuestionResult(BaseSchema):
    """문제(DB assessment_problem) 하나의 결과. 레벨 4개를 순서대로 담는다."""

    problem_id: str
    levels: list[LevelScore]
    failed_at: AxisCode | None = Field(
        default=None, description="힌트를 소진하고도 미달로 끝난 축. 완주면 null"
    )
    needs_retest: bool = Field(description="재시험 대상인지. 판정 기준은 AI 설정값")


class ReportSummary(BaseSchema):
    """문제 × 레벨 점수 매트릭스와 총계. Spring의 report.summary에 대응."""

    questions: list[QuestionResult]
    total_score: int = Field(ge=0, description="기록 점수 합계")
    max_score: int = Field(ge=0, description="문제 수 × 4레벨 × 5점")
    completed_questions: int = Field(ge=0, description="L4까지 완주한 문제 수")


class ReportVersions(BaseSchema):
    """어떤 모델·프롬프트·루브릭으로 만들었는지. 재현성 근거."""

    model_code: str
    prompt_version: str
    rubric_version: str


class ReportResult(BaseSchema):
    """보고서가 완성됐을 때의 본문."""

    report_markdown: str
    summary: ReportSummary
    curriculum_refs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="[{teachId, unitId, sourcePages}] 부족한 파트 → 교안 위치",
    )
    retest_targets: list[str] = Field(
        default_factory=list, description="재시험 대상 problemId 목록"
    )
    versions: ReportVersions


class ReportAccepted(BaseSchema):
    """202 응답. 결과는 폴링으로 가져간다."""

    job_id: str
    status: Literal["QUEUED"]


class ReportJobStatus(BaseSchema):
    """GET /reports/{jobId} 응답.

    status 값은 analysis_job.status와 같은 집합을 쓴다. 별도 어휘를 만들면
    Spring이 enum을 하나 더 들어야 하고 DB CHECK와도 어긋난다.
    """

    job_id: str
    session_id: str | None = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
    failure_reason: str | None = Field(default=None, description="FAILED일 때만 채워진다")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: ReportResult | None = Field(default=None, description="SUCCEEDED·PARTIAL일 때만")
    ai_usage: list[dict[str, Any]] = Field(default_factory=list)
