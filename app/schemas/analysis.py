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


# ── POST /analysis-inputs (백엔드 제안, 2026-08-05 · D1/D2/D3) ─────────────
#
# 검증+fetch만 담당한다 -- 분석(teaches/requirements/questionBudget)은 여전히
# POST /analyses가 받는다. `app/engines/analysis/fetch.py`의 `FetchedInput`/
# `FetchError`가 이 스키마들의 실제 데이터 원천이다.


class HeadCommit(BaseSchema):
    """GITHUB_URL fetch의 HEAD 커밋. ZIP은 커밋 개념이 없을 수 있어 이 필드 자체가 null이다."""

    sha: str
    message: str
    committed_at: datetime


class GitCommit(BaseSchema):
    """`gitHistory[]` 항목 하나. 커밋 메시지는 없다 -- fetch.py가 gitHistory엔 메시지를

    안 담는다(HeadCommit에만 있음, 텍스트 구분자 안전성 때문이기도 하다).

    D-analysis-b1(2026-08-07, 백엔드 DDL `commit_attribution` 테이블 감사 반영):
    parentSha·authoredAt·branchName·isMergeCommit·isRevertCommit·isBotCommit·
    changedLineCount 7개는 그 테이블의 NOT NULL 컬럼과 1:1 대응한다. 판정 기준은
    `app/engines/analysis/fetch.py`의 `_parse_git_log_output`/`_tag_branch_name` 참고.
    """

    sha: str
    author_name: str
    author_email: str
    committed_at: datetime
    changed_files: list[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    parent_sha: str = Field(
        description="첫 부모 커밋 SHA(merge 커밋은 mainline 기준). 부모 없는 root 커밋은 "
                    "\"0\"*40 sentinel(git pre-receive hook의 '부모 없음' 표기 관행)",
    )
    authored_at: datetime = Field(description="author date. committedAt(커밋 date)과 다른 값")
    branch_name: str = Field(
        description="이 fetch가 resolve한 브랜치를 히스토리 전체에 균일 적용한 값이다 -- "
                    "git엔 '이 커밋이 어느 브랜치 소속'이라는 개념 자체가 없어 커밋별 진짜 "
                    "소속은 아니다. 미상이면 빈 문자열",
    )
    is_merge_commit: bool = Field(description="부모가 2개 이상인지(기계적 판정, %P 부모 개수)")
    is_revert_commit: bool = Field(
        description="커밋 제목이 정확히 'Revert \"'로 시작하는지(git revert/GitHub Revert 버튼의 "
                    "자동생성 포맷). 정밀도 우선 -- 미탐은 있어도 오탐(기여도 부당 제외)은 피한다",
    )
    is_bot_commit: bool = Field(
        description="GitHub App형 봇 계정 표기(이메일 `\\d+\\+...[bot]@users.noreply.github.com` "
                    "또는 이름이 `[bot]`로 끝남)만 본다. AI 코딩 도구 사용 흔적과는 무관 -- "
                    "그건 커밋 주체가 아니라 코드 출처 문제라 이 필드의 판정 범위 밖이다",
    )
    changed_line_count: int = 0


GitHistorySource = Literal["BACKEND_SUPPLIED", "EMBEDDED_GIT", "REMOTE_DEEPEN", "NONE"]


class AnalysisInputRequest(BaseSchema):
    """ POST /api/v0/analysis-inputs 요청 본문 (백엔드 프로포절 `api-request-to-ai-server.md`
    §제안 API ① 기준. 필드명은 그 문서를 그대로 따른다 -- 합의된 계약이 아직 없어
    "이미 합의된 계약이 있다면 그쪽을 따르겠다"는 문서의 말대로, 저쪽이 확정하면 맞춘다).
    """

    request_id: str = Field(description="멱등키. 같은 값 재호출 시 같은 결과를 반환")
    method: Literal["GITHUB_URL", "ZIP_WITH_GITLOG"]
    org_id: str
    repository_url: str | None = Field(default=None, description="method=GITHUB_URL일 때 필수")
    requested_branch: str | None = Field(default=None, description="미지정 시 기본 브랜치")
    github_installation_id: str | None = Field(
        default=None,
        description="기관 GitHub App 연동 사용 시(비공개 레포). 🔴 잠정 필드 -- 이 서비스는 "
                    "아직 GitHub 인증 메커니즘이 전혀 없다(2026-08-06 확인, requirements.txt에 "
                    "관련 의존성 0건). 받기만 하고 지금은 안 쓴다",
    )
    download_url: str | None = Field(
        default=None,
        description="method=ZIP_WITH_GITLOG일 때 필수(storageUri와 최소 하나). presigned "
                    "HTTPS URL을 권장한다 -- 이 서비스에 boto3/AWS 자격증명이 전혀 없어 "
                    "s3:// 스토리지 URI는 즉시 ARCHIVE_INVALID로 거부된다",
    )
    storage_uri: str | None = Field(
        default=None, description="프로포절 원문 필드명(호환용). s3://면 위와 같이 거부된다",
    )
    git_history: list[GitCommit] | None = Field(
        default=None,
        description="D3 우선순위 ① -- 백엔드가 이미 아는 git 히스토리가 있으면 실어 보낸다. "
                    "실려 있으면 ZIP 안의 .git을 직접 파싱하는 것보다 이 값을 우선한다",
    )

    @model_validator(mode="after")
    def _check_conditional_fields(self) -> "AnalysisInputRequest":
        if self.method == "GITHUB_URL" and not (self.repository_url or "").strip():
            raise ValueError("method=GITHUB_URL에는 repositoryUrl이 필요합니다")
        if self.method == "ZIP_WITH_GITLOG" and not (
            (self.download_url or "").strip() or (self.storage_uri or "").strip()
        ):
            raise ValueError("method=ZIP_WITH_GITLOG에는 downloadUrl(또는 storageUri)이 필요합니다")
        return self


class AnalysisInputResponse(BaseSchema):
    """ POST /analysis-inputs 200 응답 """

    analysis_input_id: str = Field(
        description="D2 -- 서버가 상태 없이 결정론적으로 만든 값(같은 입력이면 항상 같은 id). "
                    "POST /analysis 요청 시 이 값과 함께 아래 필드들을 그대로 다시 실어 보내야 "
                    "한다(재fetch에 필요 -- §0.1, 이 서비스는 캐싱하지 않는다)",
    )
    method: Literal["GITHUB_URL", "ZIP_WITH_GITLOG"]
    resolved_branch: str | None = None
    head_commit: HeadCommit | None = None
    git_history: list[GitCommit] = Field(default_factory=list)
    git_history_source: GitHistorySource = "NONE"
    history_truncated: bool = Field(
        default=False,
        description="벽시계 시간 예산 안에서 히스토리를 다 못 걷었는지(D1). true여도 요청은 "
                    "실패하지 않는다 -- 코드 fetch 자체는 이 값과 무관하게 항상 완료된다",
    )
    file_count: int
    byte_count: int
    input_hash: str = Field(
        description="sha256 hex 64자. .git/** 제외 전 파일 기준(기존 snapshotMeta.contentHash와 "
                    "다른 정의 -- 그건 vendor 스캐너 변경마다 흔들려서 재fetch 무결성 검증에 "
                    "못 쓴다). 같은 트리면 ZIP으로 받든 클론으로 받든 동일하다",
    )
    captured_at: datetime


class AnalysisInputFailure(BaseSchema):
    """ POST /analysis-inputs 422 응답. 공용 ErrorResponse({error,message,retryable})와

    다른 별도 모양이다 -- 백엔드 프로포절이 {failureCode,message,requestId}를 명시했고,
    failureCode 11종은 백엔드 DB CHECK 제약의 문자열 그대로여야 한다.
    """

    failure_code: str
    message: str
    request_id: str | None = None


class AnalysisInputRef(BaseSchema):
    """`POST /analysis`가 D2 재fetch에 쓰는 서술자 -- `AnalysisInputResponse`를 백엔드가

    그대로 되돌려 보내는 모양이다(§0.1, 이 서비스는 analysisInputId를 캐싱하지 않아
    재fetch에 필요한 값을 매번 다시 받아야 한다). `fetch.refetch_pinned()`가 그대로
    소비한다.
    """

    analysis_input_id: str = Field(description="에코용 -- 재fetch 자체에는 안 쓴다")
    method: Literal["GITHUB_URL", "ZIP_WITH_GITLOG"]
    repository_url: str | None = None
    resolved_branch: str | None = None
    head_commit_sha: str | None = Field(
        default=None, description="method=GITHUB_URL이면 필수 -- 이 sha로 정확히 고정 재fetch한다",
    )
    download_url: str | None = None
    storage_uri: str | None = None
    input_hash: str = Field(
        description="재fetch 후 이 값과 다르면 하드 실패(FetchError INPUT_HASH_MISMATCH) --"
                    " 검증했던 것과 다른 코드를 분석하면 안 된다",
    )
    git_history: list[GitCommit] | None = None


class AnalysisRequest(BaseSchema):
    """ POST /api/v0/analyses 요청 본문 """

    attempt_id: str | None = Field(default=None, description="Spring 측 측정수행 키(에코용)")
    submission_id: str | None = None
    # callbackUrl은 없다 (2026-08-03 확정, PLAN §T11 D-3). 202 + 폴링으로 간다 —
    # AI→백엔드 방향 통신이 0이라 그 구간의 인증·방화벽을 새로 정할 일이 없다.
    method: Literal["GITHUB_URL", "ZIP_WITH_GITLOG"] | None = Field(
        default=None, description="analysisInput이 있으면 생략한다(그 안의 method를 쓴다)",
    )
    source: AnalysisSource = Field(default_factory=AnalysisSource)
    analysis_input: AnalysisInputRef | None = Field(
        default=None,
        description="§제안 API ①②연결(2026-08-06) -- /analysis-inputs 응답을 그대로 되돌려 "
                    "보내면 이걸로 재fetch한다(D2). method/source와 상호배타 -- 있으면 그 "
                    "둘은 무시한다(멀티파트 ZIP 업로드 대신 이 경로를 쓴다는 뜻)",
    )
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
    provider_model_code: str | None = Field(
        default=None,
        description="공급자에게 그대로 넘길 모델 식별자. 값은 `ai_model.provider_model_code` "
                    "(예: nvidia/nemotron-3-ultra-550b-a55b). 화면 선택값인 `model_code`가 "
                    "아니다 — 벤더 접두어가 붙은 원본 식별자여야 호출이 된다. "
                    "생략 시 서버 기본값. operator가 고른다",
    )
    
    @model_validator(mode="after")
    def _check_conditional_fields(self) -> "AnalysisRequest":
        """ 다른 필드 값에 따라 필수가 되는 것들을 검사

        mode="after"는 개별 필드 검증 끝난 후 실행하라는 뜻
        """
        # analysisInput이 있으면 그게 method/source를 대신한다 -- 상호배타.
        if self.analysis_input is None:
            if not self.method:
                raise ValueError("method 또는 analysisInput 중 하나가 필요합니다")
            if self.method == "GITHUB_URL" and not (self.source.repo_url or "").strip():
                raise ValueError("method=GITHUB_URL에는 source.repoUrl이 필요합니다")
        elif self.analysis_input.method == "GITHUB_URL" and not (
            self.analysis_input.head_commit_sha or ""
        ).strip():
            raise ValueError("analysisInput.method=GITHUB_URL에는 headCommitSha가 필요합니다")

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

# 문제가 가리키는 근거의 성격. **DB assessment_problem_reference.reference_type CHECK와
# 같은 집합이다** (새 MEAS, 2026-08-04에 정렬).
#
# 🔴 옛 값(`CALLEE`·`DEFINITION`·`TEST`·`CONFIG`·`SIMILAR`)은 폐기했다 — 새 정의서 CHECK에
# 없어서 그대로 보내면 **Spring INSERT가 깨진다.** 지금까지 `references[]`가 항상 빈 배열이라
# 안 터졌을 뿐이고, 채우는 순간 터졌을 자리다.
ReferenceType = Literal[
    "PRIMARY_BLOCK",       # 문제를 낸 그 지점. 화면에 띄울 본문이 여기 붙는다
    "QUESTION_HIGHLIGHT",  # 축별로 강조할 구간. axisCode가 필수다
    "CALLER",              # 이 코드를 부르는 쪽
    "RELATED_CONTEXT",     # 같이 봐야 이해되는 다른 자리 (옛 CALLEE·DEFINITION·SIMILAR가 여기로)
    "CURRICULUM_EVIDENCE", # 교안 근거. **코드 라인이 없다** — 개념이 교안 어디서 왔는지
]

class ProblemReference(BaseSchema):
    """문제가 가리키는 근거. DB `assessment_problem_reference` 대응.

    **코드 근거와 교안 근거가 한 테이블에 섞여 있다.** 유형마다 필수 필드가 다르다.

        PRIMARY_BLOCK · QUESTION_HIGHLIGHT · CALLER · RELATED_CONTEXT
            path · lineStart · lineEnd 필수 (코드 근거)
        CURRICULUM_EVIDENCE
            코드 라인이 없다. teachId 로 교안을 가리킨다
        QUESTION_HIGHLIGHT
            axisCode 필수 — 어느 축에서 강조할 구간인지
    """

    reference_type: ReferenceType
    display_order: int = Field(
        default=1, ge=1,
        description="화면에 놓는 순서. DB CHECK (> 0)",
    )
    path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    axis_code: AxisCode | None = Field(
        default=None, description="QUESTION_HIGHLIGHT일 때 필수. 어느 축의 강조 구간인가",
    )
    teach_id: str | None = Field(
        default=None, description="CURRICULUM_EVIDENCE일 때 필수. 요청 teaches[].id",
    )
    evidence_hash: str = Field(description="sha256 hex 64자")

    @model_validator(mode="after")
    def _check_type_rules(self) -> "ProblemReference":
        """유형별 필수 필드. DB CHECK와 같은 규칙이라 여기서 막지 않으면 INSERT가 깨진다."""
        if self.reference_type == "CURRICULUM_EVIDENCE":
            if not self.teach_id:
                raise ValueError("CURRICULUM_EVIDENCE에는 teachId가 필요합니다")
        elif not (self.path and self.line_start and self.line_end):
            raise ValueError(
                f"{self.reference_type}에는 path·lineStart·lineEnd가 필요합니다"
            )
        if self.reference_type == "QUESTION_HIGHLIGHT" and not self.axis_code:
            raise ValueError("QUESTION_HIGHLIGHT에는 axisCode가 필요합니다")
        if (self.line_start is not None and self.line_end is not None
                and self.line_end < self.line_start):
            raise ValueError(f"lineEnd가 lineStart보다 작습니다: {self.line_start}~{self.line_end}")
        return self
    
class Hint(BaseSchema):
    """단계 하나에 딸린 힌트(= 재질의 문장). L1·L2만 분석 때 미리 만든다."""

    hint_level: int = Field(ge=1, le=2)
    hint_text: str


# 질문·힌트를 분석 배치에서 미리 만드는 단계.
# 2026-08-02 최종 동결: **4축 전부** 분석 배치에서 만든다(전면 동결).
# 혼합 모드(L1·L2만 동결)는 폐기 — 세션 중 LLM 호출은 채점 하나뿐이다.
FROZEN_AXES = tuple(get_args(AxisCode))


class ProblemStage(BaseSchema):
    """문제 하나의 단계 하나. 4축이 곧 4단계다.

    **4축 전부 분석 배치에서 질문·힌트를 만들어 동결한다.** 세션은 저장분을 꺼내
    쓰기만 한다. 그래서 분석 응답에는 항상 질문 4개와 힌트 8개가 다 들어 있다.
    """

    axis_code: AxisCode
    question_text: str | None = Field(
        default=None,
        description="분석 때 확정된 질문. 4축 전부 필수",
    )
    flagged: bool = Field(
        default=False,
        description="보기형(①②③ 등)이 섞여 재생성에도 실패한 질문. 화면에 '검수 필요'",
    )
    hints: list[Hint] = Field(
        default_factory=list,
        description="정확히 2개(hintLevel 1, 2). 4축 전부 필수",
    )

    @model_validator(mode="after")
    def _check_axis_rules(self) -> "ProblemStage":
        """모든 단계에 질문 1개와 힌트 2개가 실렸는지 검사.

        느슨하게 "0개 또는 2개"로 두지 않는 이유: 그러면 힌트가 안 와도 통과하고,
        학생이 힌트 없이 재답변하게 된다 — 에러 없이 동작만 틀린다.
        """
        if not (self.question_text or "").strip():
            raise ValueError(f"{self.axis_code}는 분석 때 질문이 확정돼야 합니다")

        levels = [h.hint_level for h in self.hints]
        # 런타임이 hints[hintsUsed - 1]로 꺼내므로 순서가 곧 레벨이다.
        # 뒤집혀도 에러가 안 나고 점수만 틀린다.
        if levels != [1, 2]:
            raise ValueError(
                f"{self.axis_code}의 hints는 hintLevel [1, 2] 순서로 2개여야 합니다: {levels}"
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
    # 🔴 `isGeneral`은 삭제됐다 (2026-08-03 PM 결정). teach 앵커 없는 "일반 문제"를
    # 만들지 않는다 — 오퍼레이터가 고른 개념이 코드에 없으면 **그 개념은 문항 없음**이고,
    # 다른 개념으로 갈아끼우거나 지어내지 않는다. 모든 학생이 같은 개념 3개를 본다.
    title: str = Field(
        max_length=200,
        description="문제 제목. DB `assessment_problem.title`은 generation_status="
                    "GENERATED에서 NOT NULL이다 — 안 보내면 INSERT가 깨진다. "
                    "검증하는 교안 개념 이름을 쓴다(개념이 곧 문제의 주제다)",
    )
    snippet_key: str = Field(
        max_length=128,
        description="DB `assessment_problem.source_snippet_key` = "
                    "`submission.code_snippets[].snippet_key`. **두 곳을 잇는 유일한 키다** "
                    "— 문제는 원문을 중복 저장하지 않고 이 키로 제출의 스니펫을 찾는다. "
                    "같은 분석 안에서 유일하고 재분석해도 같은 코드면 같은 값이 나온다",
    )
    code_language: str = Field(
        max_length=30,
        description="DB `assessment_problem.code_language`(GENERATED면 NOT NULL). "
                    "파일 확장자에서 정한다(JAVA·PYTHON·JAVASCRIPT·YAML …). "
                    "모르는 확장자는 `UNKNOWN`이다 — 빈 문자열은 DB CHECK가 막는다",
    )
    source_path: str
    line_start: int = Field(
        description="**파일 기준 절대 줄 번호.** codeSnippet 안에서 하이라이트할 구간의 "
                    "시작이다. 화면은 파일을 그리고 이 구간을 강조한다",
    )
    line_end: int
    code_snippet: str = Field(
        description="🔴 **문제를 낸 파일 전체다**(2026-08-03 확정). 파편만 주면 학생이 "
                    "판단할 재료가 없다 — 질문이 주변 코드를 언급하는데 화면엔 선언 한 "
                    "줄만 뜨는 일이 실제로 났다. 보여줄 구간은 lineStart~lineEnd다. "
                    "예외: 파일이 100,000자를 넘으면 파편만 온다(그때도 줄 번호는 파일 기준). "
                    "**Spring이 다시 자르지 마세요** — 자를 위치는 화면이 정한다",
    )
    content_hash: str = Field(
        description="**codeSnippet 전체(파일)의 sha256 hex 64자.** DB "
                    "`submission.code_snippets[].content_hash`에 그대로 넣는다 — "
                    "그 컬럼은 `content`의 무결성 해시라 대상이 파일 전체여야 한다",
    )
    evidence_hash: str = Field(
        description="🔴 **파편(lineStart~lineEnd 구간)의 sha256 hex 64자다.** "
                    "DB `assessment_problem.code_snippet_hash`에 넣는다. "
                    "contentHash와 대상이 다르다 — 파일 전체 기준이면 무관한 한 줄 "
                    "수정에도 '근거가 바뀌었다'가 되어 판정이 쓸모없어진다. "
                    "출제 근거가 그대로인지는 이 값으로만 판정할 수 있다",
    )
    extractor_version: int = Field(
        gt=0,
        description="이 문제를 뽑은 룰 버전. 재현성 근거. "
                    "⚠️ **테이블정의서 v06에는 assessment_problem.extractor_version이 "
                    "없다**(옛 정의서에 있던 컬럼이 빠졌다). 재분석 결과가 달라졌을 때 "
                    "룰이 바뀐 건지 코드가 바뀐 건지를 이 값 하나로 가른다 — "
                    "컬럼을 두시거나 버리시거나 백엔드 판단이다",
    )
    teach_id: str | None = Field(
        default=None,
        description="이 문제가 검증하는 교안 개념(요청 teaches[].id). **항상 채워진다** — "
                    "문제는 오퍼레이터가 고른 개념에만 붙는다(2026-08-03 PM 결정). "
                    "화면의 '클래스는 L3까지, 상속은 L2까지' 같은 개념별 도달 표시가 이 값으로 붙는다",
    )
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
    verdict: Literal["PASS", "FAIL"] = Field(
        description="DB `project_requirement_assessment.result` CHECK와 같은 값이다. "
                    "🔴 옛 축약값 'P'/'F'는 폐기했다(2026-08-04) — CHECK가 "
                    "PENDING/PASS/FAIL이라 Spring이 매번 풀어야 했다. "
                    "AI는 PENDING을 보내지 않는다(판정을 못 하면 FAIL + note다)",
    )
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

    # DB `code_analysis.analysis_document` 제약이 "schema_version을 포함해야 한다"고
    # 못 박고 있다. 재분석 때 옛 문서를 어느 계약으로 읽어야 하는지가 이 값 하나로 갈린다.
    schema_version: int = Field(default=1, ge=1, description="분석 문서 계약 버전")
    overview: str
    structure: list[DocumentArea] = Field(default_factory=list)
    decision_points: list[DecisionPoint] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    
class UnmatchedTeach(BaseSchema):
    """문항을 못 만든 검증 개념. **`―`(문항 없음)의 근거다.**

    🔴 **0단(L1 미달)과 다르다.** 0단은 물어봤는데 못 푼 것이고 이건 안 물어본 것이다.
    도달 단계 컬럼에 0을 박으면 둘이 섞여 "안 물어봤다"가 "틀렸다"로 바뀐다 — NULL이어야 한다.

    AI는 이 개념을 **두 번** 찾는다(p04-3 + 실패분 재시도 1회). 그래도 못 찾으면 여기 담긴다.
    """

    teach_id: str = Field(description="요청 teaches[].id 그대로")
    reason: str = Field(description="왜 못 만들었는지. 화면에 그대로 띄워도 되는 한 문장")


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
    unmatched_teaches: list["UnmatchedTeach"] = Field(
        default_factory=list,
        description="🔴 **문항을 못 만든 개념.** 오퍼레이터가 고른 teach 중 제출 코드에서 "
                    "근거를 못 찾은 것들이다(2026-08-03 PM 결정: 지어내지 않고 '없음'으로 둔다). "
                    "`problems`에 없는 teachId를 역산하지 않도록 명시적으로 보낸다 — "
                    "화면의 개념별 도달 격자에서 `―`(문항 없음)로 그릴 값이다",
    )
    question_count_planned: int = Field(description="계획된 질문 수. 유효 문제가 적으면 축소된다")
    # D-analysis-b1(2026-08-07): 백엔드 감사 전까지 이 5개가 아예 없었다 -- fetch.py가 이미
    # 계산해서 FetchedInput에 담는데도 AnalysisResult에 배선이 안 돼 있어 응답에 한 번도
    # 안 실렸다("계산 못 함"이 아니라 "배선 누락"). git_history_source/history_truncated는
    # 백엔드가 명시 요청하진 않았지만 AnalysisInputResponse와 대칭 유지 + 운영상 유용해서
    # 같이 추가한다.
    resolved_branch: str | None = None
    head_commit: HeadCommit | None = None
    git_history: list[GitCommit] = Field(default_factory=list)
    git_history_source: GitHistorySource = "NONE"
    history_truncated: bool = False

# GET /analyses/{jobId}의 failureCode. 백엔드 실측 DDL(ck_analysis_job_failure_code_2,
# 2026-08-06 감사 회신)로 확정된 값 -- 이 11종이 전부이고 그 외 문자열은 Spring INSERT가
# 거부한다. schemas/는 engines/를 import하지 않는 계층 원칙을 유지한다 -- fetch.py의 내부
# 코드(13종, GITHUB 6 + ZIP 5 + JOB_ONLY 2)를 이 11종으로 옮기는 매핑은 `jobs.py`의
# `_FETCH_FAILURE_CODE_TRANSLATION`에 있고, 그 딕셔너리와의 drift는
# `tests/test_jobs.py`가 잡는다(fetch_engine.VERIFICATION_FAILURE_CODES|JOB_ONLY_FAILURE_CODES
# 와 정확히 같은 키 집합인지 대조).
AnalysisJobFailureCode = Literal[
    "EMPTY_CODE_EVIDENCE", "SOURCE_UNREACHABLE", "UNSUPPORTED_LANGUAGE",
    "ANALYSIS_TIMEOUT", "MODEL_ERROR", "TEMPORARY_ERROR",
    "INVALID_REPOSITORY_URL", "REPO_NOT_FOUND", "REPOSITORY_ACCESS_DENIED",
    "BRANCH_NOT_FOUND", "UNSUPPORTED_HOST",
]


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
    failure_code: AnalysisJobFailureCode | None = Field(
        default=None,
        description="백엔드 DDL(ck_analysis_job_failure_code_2)로 확정된 11종. FAILED일 때만 채워진다",
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: AnalysisResult | None = Field(default=None, description="SUCCEEDED·PARTIAL일 때만")
    # 스텁 단계에서는 항상 빈 배열이다. P02가 LLM 파이프라인으로 교체되는 중이라
    # (2026-07-29, PLAN §4) 실물 엔진이 붙으면 호출 기록이 채워진다.
    ai_usage: list[AiUsage] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_failure_code(self) -> "AnalysisJobStatus":
        """AiUsage._check_db_constraints와 같은 패턴 -- FAILED와 failureCode는 항상 같이 다닌다.

        🔴 이 검증은 **생성 시점에만** 돈다. `jobs.py`는 이미 만든 job을 필드별로
        나중에 mutate한다(`validate_assignment`를 켜지 않았다 -- 다른 스키마 전부에
        영향이 가는 전역 변경이라 이 작업 범위를 넘는다). 그래서 실제 방어는
        `jobs.py`가 status="FAILED"를 설정하는 자리마다 failure_code를 같이
        설정하는 절차적 규율로 한다 -- 이 validator는 그 계약을 문서화하고, 직접
        생성하는 코드(테스트 등)에서는 실제로 걸러낸다.
        """
        if self.status == "FAILED" and self.failure_code is None:
            raise ValueError("status=FAILED에는 failureCode가 필요합니다")
        if self.status != "FAILED" and self.failure_code is not None:
            raise ValueError("status=FAILED가 아니면 failureCode가 없어야 합니다")
        return self