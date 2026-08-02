""" 보고서 API의 요청/응답 스키마. 표기는 camelCase

이전 grading.py(세션 종료 후 5축 후채점)를 대체한다. P04에서 채점이 문답 도중
레벨마다 일어나므로 세션이 끝난 시점에 따로 채점할 것이 없다. 남는 비동기 작업은
보고서 생성(LLM + 교안 참조 조회)뿐이라 리소스 이름을 그것에 맞췄다.
"""
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema
from app.schemas.usage import AiUsage

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
    """POST /reports 요청. **문제 하나가 끝날 때마다 한 번씩** 부른다.

    2026-08-02 확정: 보고서는 세션 단위가 아니라 **문제 단위**다. 세션 1회에
    문제 3개면 보고서도 3개다. 학생이 다음 문제를 푸는 동안 병렬로 돌리므로
    학생 체감 대기가 0이다 — 세션 끝에 몰아 만들면 그만큼 기다리게 된다.
    """

    problem_id: str = Field(description="이 보고서가 다루는 문제. 문제 단위의 키")
    session_id: str | None = None
    score_run_id: str | None = Field(default=None, description="Spring ScoreRun 키(에코용)")
    transcript: list[dict[str, Any]] = Field(
        default_factory=list,
        description="**이 문제의 턴만.** 점수가 이미 확정된 기록 (최대 4단계 × 3시도)",
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
    """문제(DB assessment_problem) 하나의 결과. 단계 4개를 순서대로 담는다.

    🔴 **총점이 없다** (2026-08-02, PM 설계 v2 §5-1). 이유 둘:

    ① **이 구인은 보상을 허용하지 않는다.** 자기 코드가 무엇을 하는지 설명하지
       못하는데 대안을 잘 알아서 총점이 높은 상태는 성립하면 안 된다.
    ② **총합은 결측을 0으로 만든다.** L1에서 막혀 L2~L4를 받지 않은 학생의 나머지를
       0으로 계산하면 "못한 것"과 "안 물어본 것"이 섞인다.

    대신 `reachedStage`가 판정값이다. 축별 0~5점은 그대로 보낸다 — 저장·정렬
    tie-break·재채점 실험의 입력이다(화면에 표기할지는 프론트가 정한다).
    """

    problem_no: int = Field(ge=1)
    problem_id: str
    reached_stage: int = Field(
        ge=0, le=4,
        description="통과한 최고 단계. 0=L1 미달 · 1=L1까지 · 2=L2까지 · 3=L3까지 · 4=완주. "
                    "위험·우수·재시험 판정이 전부 이 값으로 돈다",
    )
    stages: list[StageScore] = Field(
        min_length=4, max_length=4,
        description="항상 4개. 도달 못 한 단계도 attemptCount=0으로 채워 보낸다",
    )

    @model_validator(mode="after")
    def _check_stages(self) -> "ProblemResult":
        """L1→L4 순서로 한 벌인지, reachedStage가 실제 통과 기록과 맞는지 검사.

        DB problem_stage가 문제당 4행으로 미리 만들어져 있다. 도달 못 한 단계를
        빼고 보내면 Spring이 어느 행을 채울지 몰라 순서로 짐작하게 되고,
        그때 L3 점수가 L4 행에 들어간다 — 에러 없이 점수만 틀린다.

        reachedStage는 파생값이라 따로 보내면 어긋날 수 있다. 어긋난 채로 나가면
        화면에 뜨는 판정과 근거가 다른 말을 한다 — 여기서 막는다.
        """
        axes = [s.axis_code for s in self.stages]
        expected = list(get_args(AxisCode))
        if axes != expected:
            raise ValueError(f"stages의 axisCode는 {expected} 순서여야 합니다: {axes}")

        # 계단이라 중간에 건너뛴 통과는 없다. 앞에서부터 연속으로 통과한 개수가 도달 단계다.
        reached = 0
        for stage in self.stages:
            if not stage.passed:
                break
            reached += 1
        if reached != self.reached_stage:
            raise ValueError(
                f"reachedStage가 통과 기록과 다릅니다: {self.reached_stage} != {reached}"
            )
        return self


class ReportVersions(BaseSchema):
    """어떤 모델·프롬프트·루브릭으로 만들었는지. 재현성 근거."""

    model_code: str
    prompt_version: str
    rubric_version: str


class ReportResult(BaseSchema):
    """보고서가 완성됐을 때의 본문. **문제 하나 분량이다.**"""

    report_markdown: str
    problem: ProblemResult
    curriculum_refs: list[dict[str, Any]] = Field(default_factory=list)
    retest: bool = Field(
        description="이 문제가 재시험 대상인가. **L1·L2 둘 다 통과해야 아니다** "
                    "(scoring.RETEST_TRIGGER_AXES). 세션 전체의 재시험 여부는 "
                    "Spring이 문제 3개의 이 값을 모아 판단한다",
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
    problem_id: str | None = Field(default=None, description="이 job이 다루는 문제")
    session_id: str | None = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
    failure_reason: str | None = Field(default=None, description="FAILED일 때만 채워진다")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: ReportResult | None = Field(default=None, description="SUCCEEDED·PARTIAL일 때만")
    ai_usage: list[AiUsage] = Field(default_factory=list)
