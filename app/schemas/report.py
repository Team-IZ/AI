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
# DB problem_stage.axis_code CHECK와 같은 값이라 그대로 INSERT된다.
# 나열 순서가 곧 진행 순서다 — reports.py가 get_args()로 이 순서를 읽는다.
# 점수는 0~5. 0은 무응답이거나 질문과 무관한 답.
AxisCode = Literal[
    "L1",  # 코드 기술 — 무엇을 하는 코드인가
    "L2",  # 설계 논리 — 왜 그렇게 했는가
    "L3",  # 대안 비교 — 다른 방법과 비교해 왜 이것인가
    "L4",  # 반례·한계 — 언제 깨지는가
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


class StageScore(BaseSchema):
    """문제 하나의 단계 하나. DB problem_stage 대응."""

    axis_code: AxisCode
    attempt_count: int = Field(
        ge=0, le=3, description="문답 시도 횟수. 0이면 앞 단계에서 끝나 도달하지 못했다"
    )
    passed: bool = Field(description="confirmedScore가 통과선(3점) 이상인지")
    best_score: int | None = Field(
        default=None, ge=0, le=5, description="힌트 상한 적용 전 원점수. 미도달이면 null"
    )
    confirmed_score: int | None = Field(
        default=None, ge=0, le=5, description="힌트 상한 적용 후 기록 점수. 미도달이면 null"
    )
    # attemptCount - 1 과 같지만 직접 보낸다. 미도달(0)일 때 -1이 되는 것을 막는다.
    hints_used: int = Field(default=0, ge=0, le=2)
    autonomy: AutonomyCode | None = None


class ProblemResult(BaseSchema):
    """문제(DB assessment_problem) 하나의 결과. 단계 4개를 순서대로 담는다."""

    problem_no: int = Field(ge=1)
    problem_id: str
    total_score: int = Field(ge=0, description="stages의 confirmedScore 합")
    max_score: int = Field(ge=0, description="4단계 × 5점 = 20")
    stages: list[StageScore]


class ReportVersions(BaseSchema):
    """어떤 모델·프롬프트·루브릭으로 만들었는지. 재현성 근거."""

    model_code: str
    prompt_version: str
    rubric_version: str


class ReportResult(BaseSchema):
    """보고서가 완성됐을 때의 본문."""

    report_markdown: str
    problems: list[ProblemResult]          # summary: ReportSummary 였던 자리
    curriculum_refs: list[dict[str, Any]] = Field(...)   # 그대로
    retest_targets: list[str] = Field(...)               # 그대로
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
