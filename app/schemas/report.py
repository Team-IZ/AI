""" 보고서 API의 요청/응답 스키마. 표기는 camelCase

이전 grading.py(세션 종료 후 5축 후채점)를 대체한다. P04에서 채점이 문답 도중
레벨마다 일어나므로 세션이 끝난 시점에 따로 채점할 것이 없다. 남는 비동기 작업은
보고서 생성(LLM + 교안 참조 조회)뿐이라 리소스 이름을 그것에 맞췄다.
"""
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema, UuidStr
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

    problem_id: UuidStr = Field(description="이 보고서가 다루는 문제. 문제 단위의 키")
    problem_no: int | None = Field(
        default=None, ge=1,
        description="세션 안에서 이 문제가 몇 번째인가(1~3). 응답 problem.problemNo로 "
                    "그대로 돌아온다. 생략하면 1이 나가는데, 2·3번 문제의 보고서에도 "
                    "1이 찍혀 화면 표기와 어긋난다",
    )
    session_id: UuidStr | None = None
    score_run_id: str | None = Field(default=None, description="Spring ScoreRun 키(에코용)")
    provider_model_code: str | None = Field(
        default=None,
        description="공급자에게 그대로 넘길 모델 식별자. 값은 `ai_model.provider_model_code`"
                    "(벤더 접두어 포함). 생략 시 서버 기본값 — `/analyses`·`/curricula`와 "
                    "같은 규칙이다. 모델 선택은 operator 권한이다",
    )
    transcript: list[dict[str, Any]] = Field(
        default_factory=list,
        description="**이 문제의 축별 채점 결과.** `problem_stage` 행을 그대로 보낸다 — "
                    "축당 1개, 최대 4개(L1~L4). 한 항목이 슬롯 3개를 평평하게 담는다: "
                    "`axisCode` · `status` · `questionText`/`questionAnswerText`/"
                    "`questionScore`/`questionPassed` · `firstHint...` · `secondHint...`. "
                    "답이 없는 슬롯은 null로 두거나 생략한다. "
                    "🔴 2026-08-10 모양 변경: 예전 **턴 목록**(`hintsUsed`로 슬롯을 고르는 "
                    "축당 최대 3개)은 더는 안 받는다. 보내면 슬롯 필드가 없어 그 축이 "
                    "NOT_REACHED로 계산된다. **응답 `problem.stages`와 같은 모양이라** "
                    "Spring은 읽은 행을 그대로 보내고 받은 행을 그대로 INSERT하면 된다. "
                    "`status`는 보내주면 그대로 쓴다 — `NOT_ANSWERED`(물었는데 답이 없다)는 "
                    "AI가 만들 수 없어서 세션 진행을 아는 백엔드 값이 정확하다. "
                    "생략하면 점수 유무로 계산한다",
    )
    analysis_documents: list[dict[str, Any]] = Field(
        default_factory=list, description="[{kind, content}] 코드 분석 문서"
    )
    teaches: list[dict[str, Any]] = Field(
        default_factory=list, description="[{id, label, unitId, sourcePages}] 교안 참조용"
    )


# DB problem_stage.status. 이 값을 그대로 쓴다 — 별도 어휘를 만들면 Spring이 매핑을 든다.
StageStatus = Literal[
    "PREPARED",      # 질문·힌트만 채워진 초기 상태
    "IN_PROGRESS",   # 답변이 시작됐다
    "PASSED",        # 질문·힌트1·힌트2 중 하나가 통과
    "NOT_PASSED",    # 셋 다 미통과 (= 이 축에서 문제가 끝났다)
    "NOT_REACHED",   # 앞 단계에서 끝나 묻지 않았다
    "NOT_ANSWERED",  # 물었는데 답이 없다 (시간 초과 등)
]


class StageScore(BaseSchema):
    """문제 하나의 단계 하나. **DB `problem_stage` 한 행과 1:1이다** (2026-08-03).

    새 MEAS 정의서에서 `problem_stage`가 **한 행에 질문 1 + 힌트 2 + 답변 3 + 점수 3 +
    통과 3**을 담게 됐다(`stage_answer_attempt` 테이블은 사라졌다). 그래서 응답도 같은
    모양으로 낸다 — **Spring이 변환 없이 그대로 INSERT할 수 있게** 하는 것이 목적이다.

    🔴 **`attemptCount`·`hintsUsed`·`autonomy`는 없다.** 셋 다 아래 6개 필드에서
    파생된다(#42 §4-2). 자력 판정은 이렇게 계산한다.

        questionPassed = true    → 스스로 답함      (SELF)
        firstHintPassed = true   → 힌트 1개로 답함  (SELF_MAINTAINED)
        secondHintPassed = true  → 힌트 2개로 답함  (PARTIAL)
    """

    axis_code: AxisCode

    question_score: int | None = Field(
        default=None, ge=0, le=5, description="질문 답변 점수. 미응답·미도달이면 null"
    )
    question_passed: bool | None = Field(
        default=None, description="score >= 3. 점수와 함께 존재하거나 함께 null"
    )
    first_hint_score: int | None = Field(
        default=None, ge=0, le=5,
        description="첫 힌트 후 답변 점수. **질문이 미통과일 때만 값이 있다**(DB CHECK)",
    )
    first_hint_passed: bool | None = None
    second_hint_score: int | None = Field(
        default=None, ge=0, le=5,
        description="둘째 힌트 후 답변 점수. **첫 힌트가 미통과일 때만 값이 있다**(DB CHECK)",
    )
    second_hint_passed: bool | None = None

    status: StageStatus = Field(description="DB problem_stage.status 값을 그대로 쓴다")

    @property
    def passed(self) -> bool:
        """이 단계를 통과했는가. 세 슬롯 중 하나라도 통과면 통과다."""
        return bool(self.question_passed or self.first_hint_passed or self.second_hint_passed)


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

    problem_no: int = Field(
        ge=1,
        description="요청의 problemNo를 그대로 돌려준다. 안 보내면 1이다 — "
                    "**보내지 않으면 화면의 '문제 2 / 3'과 어긋난다**",
    )
    problem_id: UuidStr
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

    # aiUsage.modelCode와 같은 값이다 — 호출에 쓴 provider 문자열을 그대로 에코한다.
    model_code: str
    prompt_version: str
    rubric_version: str


class ReportNote(BaseSchema):
    """서술 한 덩어리. `strengths`·`gaps`가 같은 모양이라 하나로 쓴다."""

    axis: str = Field(description="모델이 붙인 이름표. 축 코드(L1~L4)가 아니라 교안 개념명이 온다")
    detail: str = Field(description="실제 답변을 근거로 한 서술")
    teach_id: UuidStr | None = Field(
        default=None,
        description="gaps에만 있다. **요청 teaches에 없는 id는 버리고 null로 만든다** — "
                    "모델이 지어낸 교안을 가리키면 학생이 없는 페이지를 뒤진다",
    )
    study_pointer: str | None = Field(
        default=None,
        description="'어느 unit 몇 페이지를 다시 볼 것인가'의 서술. 화면의 교안 링크는 "
                    "이 문장이 아니라 `curriculumRefs`로 그려야 한다 — 이 문장은 검증을 "
                    "거치지 않은 모델 출력이다",
    )


class ReportNarrative(BaseSchema):
    """`reportMarkdown`을 만들기 전의 구조. **같은 내용을 두 모양으로 준다.**

    마크다운 하나만 주면 "잘한 것만 카드로" 같은 화면을 만들 때 프론트가 헤딩을
    다시 파싱해야 한다. 모델이 이미 이 구조로 답하므로 노출 비용이 0이다
    (2026-08-03 확정). 마크다운도 계속 준다 — 그냥 렌더만 하면 되는 경로를
    남겨두는 편이 프론트 미착수 시점에 싸다.

    **`narrativeFailed: true`면 이 객체는 전부 비어 있다.** 판정(`problem`)은 그때도
    확정값이다.
    """

    summary: str | None = None
    strengths: list[ReportNote] = Field(default_factory=list)
    gaps: list[ReportNote] = Field(default_factory=list)
    autonomy_note: str | None = Field(
        default=None,
        description="힌트 없이 답한 부분과 힌트를 받고서야 답한 부분의 차이",
    )
    unreached_axes: list[AxisCode] = Field(
        default_factory=list,
        description="앞 단계에서 끝나 묻지 않은 축. **'못한 것'이 아니라 '안 물어본 것'이다** — "
                    "화면에서 0점이나 실패로 그리면 안 된다",
    )


class ReportResult(BaseSchema):
    """보고서가 완성됐을 때의 본문. **문제 하나 분량이다.**"""

    report_markdown: str
    narrative: ReportNarrative
    problem: ProblemResult
    curriculum_refs: list[dict[str, Any]] = Field(default_factory=list)
    retest: bool = Field(
        description="이 문제가 재시험 대상인가. **L1·L2 둘 다 통과해야 아니다** "
                    "(scoring.RETEST_TRIGGER_AXES). 세션 전체의 재시험 여부는 "
                    "Spring이 문제 3개의 이 값을 모아 판단한다",
    )
    narrative_failed: bool = Field(
        default=False,
        description="서술 생성(LLM)이 실패해 reportMarkdown에 판정만 들어 있는가. "
                    "**true여도 job은 SUCCEEDED다** — 점수·도달·재시험은 LLM 없이 "
                    "계산되므로 확정값이다. 서술이 필요하면 같은 요청을 다시 보내면 "
                    "된다(무료 티어에서 실제로 발생한다)",
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
    problem_id: UuidStr | None = Field(default=None, description="이 job이 다루는 문제")
    session_id: UuidStr | None = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
    failure_reason: str | None = Field(default=None, description="FAILED일 때만 채워진다")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: ReportResult | None = Field(default=None, description="SUCCEEDED·PARTIAL일 때만")
    ai_usage: list[AiUsage] = Field(default_factory=list)
