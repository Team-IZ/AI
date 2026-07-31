""" 코드 분석 API(P02)의 요청 응답 스키마"""
from datetime import datetime
from typing import Any, Literal, get_args
from app.schemas.report import AxisCode
from app.schemas.usage import AiUsage

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema

class AnalysisSource(BaseSchema):
    repo_url: str | None = Field(
        default=None, description="method=GITHUB_URL일 때 필수. 공개 레포만 지원"
    )
    branch: str | None = Field(default=None, description="생략 시 기본 브랜치")
    
class FocusItem(BaseSchema):
    """강사가 지정한 질문 초점 후보. Spring question_focus_item 행 대응.

    AI는 이 중 하나의 id를 골라 problem.questionFocusItemId로 돌려준다.
    PK가 랜덤 UUID라 AI 코드에 값을 박을 수 없어서 후보를 받아 고르는 방식이다(C-1).
    """

    id: str
    name: str
    
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
    question_budget: int = Field(default=3, ge=1, description="계획 문제 수")
    focus_items: list[FocusItem] = Field(
        default_factory=list, description="강사 지정 초점 후보. 비면 AI 자율 선정"
    )
    requirements: list[dict[str, Any]] = Field(
        default_factory=list, description="[{requirementId, text}] P/F 판정 대상"
    )
    teaches: list[dict[str, Any]] = Field(
        default_factory=list, description="[{id, label, unitId, sourcePages}] 교안 참조용"
    )
    curriculum_id: str | None = None
    model_code: str | None = Field(
        default=None, description="생략 시 서버 기본값. operator가 고른다"
    )
    
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

# 문제 지점 주변에서 같이 봐야 하는 코드의 성격.
# 주 지점 자체는 Problem이 갖는다(PRIMARY 폐기 — 같은 위치가 두 군데 적히는 것을 막는다).
ReferenceType = Literal[
    "CALLER",      # 이 코드를 부르는 쪽
    "CALLEE",      # 이 코드가 부르는 쪽
    "DEFINITION",  # 여기서 쓰는 타입·상수의 정의
    "TEST",        # 이 코드를 검증하는 테스트
    "CONFIG",      # 동작을 좌우하는 설정
    "SIMILAR",     # 비슷한 처리를 하는 다른 자리 (L3 대안 질문의 재료)
]

class ProblemReference(BaseSchema):
    """문제가 가리키는 코드 위치. DB problem_reference 대응."""

    path: str
    line_start: int
    line_end: int
    evidence_hash: str = Field(description="sha256 hex 64자")
    reference_type: ReferenceType
    
class Hint(BaseSchema):
    """단계 하나에 딸린 힌트(= 재질의 문장). L1·L2만 분석 때 미리 만든다."""

    hint_level: int = Field(ge=1, le=2)
    hint_text: str


# 질문·힌트를 분석 배치에서 미리 만드는 단계.
# 2026-07-31 PM 확정: L1·L2는 동결, L3·L4는 세션 중 직전 답변을 근거로 적응 생성.
FROZEN_AXES = ("L1", "L2")


class ProblemStage(BaseSchema):
    """문제 하나의 단계 하나. 4축이 곧 4단계다.

    **단계마다 채워지는 시점이 다르다.**

        L1·L2   분석 배치에서 질문·힌트를 만들어 동결한다
        L3·L4   세션 중에 만든다 — 직전 단계 답변과 채점 근거를 봐야 하기 때문이다.
                분석 응답에서는 questionText가 null이고 hints가 빈 배열이다

    구조(4단계)는 분석 시점에 확정되고 내용만 나중에 채워진다. 그래서 stages는
    항상 4개이고, 아래 검증이 "어느 단계가 언제 채워지는지"를 스펙에 드러낸다.
    """

    axis_code: AxisCode
    question_text: str | None = Field(
        default=None,
        description="L1·L2는 분석 때 확정. L3·L4는 세션 중 생성되므로 여기서는 null",
    )
    flagged: bool = Field(
        default=False,
        description="보기형(①②③ 등)이 섞여 재생성에도 실패한 질문. 화면에 '검수 필요'",
    )
    hints: list[Hint] = Field(
        default_factory=list,
        description="L1·L2는 정확히 2개(hintLevel 1, 2). L3·L4는 빈 배열",
    )

    @model_validator(mode="after")
    def _check_axis_rules(self) -> "ProblemStage":
        """단계별로 채워져야 할 것과 비어 있어야 할 것을 검사.

        느슨하게 "0개 또는 2개"로 두지 않는 이유: 그러면 L1에 힌트가 안 와도,
        L3에 힌트가 와도 통과한다. 전자는 학생이 힌트 없이 재답변하게 되고
        후자는 적응 생성분을 덮어쓴다 — 둘 다 에러 없이 동작만 틀린다.
        """
        frozen = self.axis_code in FROZEN_AXES

        if frozen:
            if not (self.question_text or "").strip():
                raise ValueError(f"{self.axis_code}는 분석 때 질문이 확정돼야 합니다")
            levels = [h.hint_level for h in self.hints]
            # 런타임이 hints[hintsUsed - 1]로 꺼내므로 순서가 곧 레벨이다.
            # 뒤집혀도 에러가 안 나고 점수만 틀린다.
            if levels != [1, 2]:
                raise ValueError(
                    f"{self.axis_code}의 hints는 hintLevel [1, 2] 순서로 2개여야 합니다: {levels}"
                )
        else:
            if self.question_text is not None:
                raise ValueError(
                    f"{self.axis_code} 질문은 세션 중에 만듭니다. 분석 응답에서는 null이어야 합니다"
                )
            if self.hints:
                raise ValueError(
                    f"{self.axis_code} 힌트는 세션 중에 만듭니다. 분석 응답에서는 빈 배열이어야 합니다"
                )
        return self


# 왜 이 지점을 문제로 골랐는지의 분류. DB에 CHECK는 없지만 값을 흘리지 않는다.
ProblemType = Literal[
    "DESIGN_CHOICE",          # 설계 선택지가 있던 자리
    "RISK_POINT",             # 실패·보안 위험
    "COMPLEXITY_HOTSPOT",     # 복잡도가 몰린 곳
    "REQUIREMENT_IMPL",       # 요구사항이 실제로 구현된 자리
    "EXTERNAL_INTEGRATION",   # 외부 의존 경계
]


class Problem(BaseSchema):
    """출제 대상 코드 지점. DB assessment_problem 대응."""

    problem_id: str
    problem_no: int = Field(ge=1, description="문제 순번 1~3. 화면·보고서가 이것으로 가리킨다")
    # 문답 진행 상태다. 분석이 만드는 것은 전부 READY.
    # (후보 선별 상태 CANDIDATE/USED/SKIPPED는 DB CHECK에 없어 보내면 INSERT가 깨진다)
    status: Literal["READY", "IN_PROGRESS", "COMPLETED", "TERMINATED"] = "READY"
    problem_type: ProblemType
    priority: float
    question_focus_item_id: str | None = Field(
        default=None,
        description="요청 focusItems[].id를 그대로 돌려준다. 강사 지정 없이 뽑았으면 null",
    )
    source_path: str
    line_start: int
    line_end: int
    code_snippet: str = Field(
        description="evidenceHash를 계산한 원문 그대로. Spring이 다시 자르면 해시가 어긋난다"
    )
    evidence_hash: str = Field(description="codeSnippet의 sha256 hex 64자")
    extractor_version: str = Field(description="이 문제를 뽑은 룰 버전. 재현성 근거")
    references: list[ProblemReference] = Field(default_factory=list)
    stages: list[ProblemStage] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _check_stages(self) -> "Problem":
        """단계가 L1→L4 순서로 정확히 한 벌인지 검사.

        문답이 이 순서로 진행하고(L1 통과해야 L2) 루브릭도 축으로 붙는다.
        순서가 어긋나면 L3 답변을 L4 기준으로 채점한다 — 에러 없이 점수만 틀린다.
        """
        axes = [s.axis_code for s in self.stages]
        expected = list(get_args(AxisCode))
        if axes != expected:
            raise ValueError(f"stages의 axisCode는 {expected} 순서여야 합니다: {axes}")
        return self

class RequirementResult(BaseSchema):
    """요구사항 하나의 P/F 판정. 요청 requirements와 1:1로 대응한다."""

    requirement_id: str
    verdict: Literal["P", "F"]
    evidence: str | None = Field(default=None, description="판정 근거가 된 코드 위치·인용")
    note: str | None = Field(default=None, description="판정 실패 등 특이사항")

class DocumentArea(BaseSchema):
    """분석 문서의 구조 항목 하나. 코드를 영역 단위로 묶어 설명한다."""

    area: str
    files: list[str] = Field(description="이 영역에 속한 실제 파일 경로")
    role: str


class DecisionPoint(BaseSchema):
    """판단이 개입된 지점. 문제 3개는 여기서 골라 뽑는다(전부 문제가 되지는 않는다).

    lineStart/lineEnd는 LLM이 센 값이 아니다. LLM은 symbol(소스에 실제로 있는
    코드 한 줄을 문자 그대로 복사한 것)만 주고, 그 문자열을 실제 파일에서 찾아
    우리가 산정한다. 못 찾으면 evidenceValid=false로 남기고 줄 번호를 비운다 —
    지어낸 위치를 근거로 보여주면 "코드 파편이 곧 근거"라는 전제가 무너진다.
    """

    title: str
    source_path: str
    symbol: str = Field(description="LLM이 소스에서 문자 그대로 복사한 코드 한 줄")
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    why_it_matters: str
    related_teach_id: str | None = None
    evidence_valid: bool = Field(description="symbol을 실제 소스에서 찾았는지")
    
    @model_validator(mode="after")
    def _check_evidence(self) -> "DecisionPoint":
        """근거를 못 찾았으면 줄 번호가 남아 있으면 안 된다.

        evidenceValid=false인데 lineStart가 채워져 있으면 백엔드는 그 값을 그대로
        화면에 그린다 — 지어낸 위치가 근거처럼 보인다. 플래그만으로는 못 막는다.
        """
        if not self.evidence_valid and (self.line_start is not None or self.line_end is not None):
            raise ValueError("evidenceValid=false면 lineStart/lineEnd는 비어야 합니다")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError(f"lineEnd가 lineStart보다 작습니다: {self.line_start}~{self.line_end}")
        return self


class AnalysisDocument(BaseSchema):
    """코드 분석 문서. Markdown이 아니라 구조화 JSON이 원본이다.

    문제 선정(p04-3)과 보고서(p04-6)가 이 JSON을 그대로 프롬프트에 다시 넣는다.
    Markdown으로 저장하면 넣을 때 JSON→MD, 꺼낼 때 MD→JSON으로 두 번 변환해야 하고
    두 번째는 파싱이라 깨진다. 사람이 읽는 화면은 이걸 렌더한 결과다.
    """

    overview: str
    structure: list[DocumentArea] = Field(default_factory=list)
    decision_points: list[DecisionPoint] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    
class AnalysisResult(BaseSchema):
    """분석이 성공했을 때의 결과 본문.

    문제·질문·힌트가 전부 여기 실린다. 질문과 힌트는 분석 때 만들어 동결하므로
    세션 시작에는 AI 호출이 없다 — Spring이 이 응답을 저장해두고 꺼내 쓴다.
    """

    snapshot_id: str = Field(description="Spring code_snapshot 행의 키")
    snapshot_meta: SnapshotMeta
    applied_scope: Literal["TOTAL", "OWN_COMMIT"]
    scope_fallback: bool = Field(description="요청 범위를 못 지켜 TOTAL로 물러났는지")
    fallback_reason: str | None = None
    commit_sha: str | None = None
    analysis_document: AnalysisDocument = Field(
        description="code_analysis.analysis_document 대응. Markdown이 아니라 구조화 JSON이다"
    )
    requirement_results: list[RequirementResult] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
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
    ai_usage: list[AiUsage] = Field(default_factory=list)