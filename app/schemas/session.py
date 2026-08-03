""" 문답 세션 API(P03)의 요청 응답 스키마.

🔴 **세션은 무상태다** (2026-08-03 확정, PLAN §T11). 엔드포인트는
`POST /sessions/{id}/answers` 하나뿐이고 AI는 세션을 들고 있지 않는다.
**매 요청이 곧 restore**다 — 문제·기록·커서가 전부 요청에 실려 온다.

그래서 배포·재시작·인스턴스 중지가 진행 중인 세션을 깨지 않는다. 대신
진행 규칙(통과선·힌트 상한·사다리)은 여전히 AI가 소유한다. 커서를 입력으로 받아
다음 커서를 계산해 돌려주는 순수 함수다.
"""
from typing import Any, Literal

from pydantic import Field

from app.schemas.analysis import Problem
from app.schemas.common import BaseSchema
from app.schemas.report import AutonomyCode, AxisCode  # 축 = 문답 레벨. 정의는 report.py 한 곳뿐
from app.schemas.usage import AiUsage


class CodeContext(BaseSchema):
    """ 질문이 가르키는 코드 발췌 """
    path: str
    snippet: str
    line_start: int

class Question(BaseSchema):
    """지금 물어보는 질문 하나.

    질문은 **분석 때 동결돼 DB에 있다.** 세션은 그걸 꺼내 보여줄 뿐 만들지 않는다.
    같은 단계를 다시 물을 때도 `questionText`는 그대로고 `hintText`만 붙는다 —
    힌트는 재진술이라 원 질문을 대체하지 않는다(scoring.HINT_LADDER).
    """
    problem_id: str               # DB assessment_problem 키
    axis_code: AxisCode           # 문답 깊이(축) L1~L4
    sequence_no: int
    question_text: str
    code_context: CodeContext | None = None
    hint_text: str | None = Field(
        default=None, description="재질의면 직전에 보여줄 힌트. 첫 시도면 null",
    )
    hints_used: int = Field(
        default=0, ge=0, le=2,
        description="이 단계에서 지금까지 쓴 힌트 수. 화면의 '2번 쓸 수 있어요' 표기 근거",
    )

class Progress(BaseSchema):
    problem_index: int            # 몇 번째 문제인지(1부터)
    problem_total: int


class Cursor(BaseSchema):
    """문답이 지금 서 있는 자리. **요청과 응답에 같은 모양으로 오간다.**

    백엔드는 응답의 `cursor`를 저장해 두었다가 다음 요청에 그대로 실어 보내면 된다.
    생략하면 AI가 `transcript`를 되짚어 복원한다(첫 답변이면 첫 문제의 L1).
    """

    problem_id: str
    axis_code: AxisCode
    hints_used: int = Field(
        default=0, ge=0, le=2, description="이 단계에서 이미 연 힌트 수. 점수 상한(5/4/3)의 근거",
    )


class TranscriptTurn(BaseSchema):
    """확정된 문답 한 턴. Spring이 즉시 영속화하는 복구 근거.

    한 턴 = 질문 1개 + 답변 1개 + 채점 1개. 힌트 후 재질의는 별도 턴이 되고
    attempt_no가 올라간다(DB stage_answer_attempt 한 행에 대응).
    """
    problem_id: str
    axis_code: AxisCode
    question_text: str
    answer_text: str
    answered_at: str

    # 채점 결과. 단계마다 즉시 매겨진다.
    best_score: int = Field(ge=0, le=5, description="힌트 상한 적용 전 원점수")
    confirmed_score: int = Field(ge=0, le=5, description="힌트 상한 적용 후 기록 점수")
    attempt_count: int = Field(
        ge=1, le=3, description="이 단계에서 몇 번째 시도인지. 첫 답변이 1"
    )
    hint_text: str | None = Field(
        default=None, description="이 턴 직전에 보여준 힌트. 첫 시도면 null"
    )
    autonomy: AutonomyCode | None = None


class AnswerSubmit(BaseSchema):
    """POST /sessions/{id}/answers 요청. **세션 API는 이것 하나뿐이다.**

    AI가 상태를 안 들고 있으므로 채점에 필요한 것이 전부 여기 실려야 한다.
    """

    client_request_id: str = Field(description="세션 내 유일 멱등키. 재전송하면 같은 응답이 온다")
    answer_text: str
    problems: list[Problem] = Field(
        default_factory=list,
        description="분석이 동결한 문제 3개. 질문 4개 + 힌트 8개가 문제마다 실려 있다. "
                    "**AI는 세션 중에 질문을 만들지 않는다** — 안 주면 물을 것이 없다",
    )
    transcript: list[TranscriptTurn] = Field(
        default_factory=list,
        description="지금까지 확정된 턴 전부. `cursor`를 생략하면 이걸 되짚어 위치를 복원한다",
    )
    cursor: Cursor | None = Field(
        default=None, description="이 답변이 대답하는 자리. 생략 시 transcript로 복원",
    )
    provider_model_code: str | None = Field(
        default=None,
        description="채점에 쓸 모델. 값은 `ai_model.provider_model_code`(벤더 접두어 포함). "
                    "생략 시 서버 기본값 — `/analyses`·`/curricula`·`/reports`와 같은 규칙이다. "
                    "채점 모델은 operator가 고른다(GradingPolicy)",
    )
    analysis_context: dict[str, Any] | None = Field(
        default=None,
        description="채점기가 코드 파편 밖을 보게 하는 최소 맥락. **분석 문서 전체가 아니라 "
                    "`{overview, structure}` 두 필드만 보낸다** — `decisionPoints`는 문제 "
                    "후보 목록이라 채점에 쓸모가 없고 부피의 대부분이다(20KB → 1~2KB). "
                    "생략하면 파편만으로 채점한다",
    )


class AnswerResult(BaseSchema):
    """POST /sessions/{id}/answers 응답.

    **transcript를 통째로 돌려주지 않는다.** 요청이 이미 들고 온 것이라 되돌리면
    같은 32KB를 두 번 실어 나르게 된다. 이번 턴(`turn`)만 주므로 백엔드는 그것만
    붙여 저장하면 된다.
    """

    session_id: str
    # DB assessment_session.status CHECK와 같은 집합.
    # READY = 분석 직후 미리 만들어 둔 상태(문제보다 세션이 먼저 있어야 한다).
    # TIMEOUT은 DB에 없다 — 시간 초과는 EXPIRED다.
    state: Literal[
        "READY", "IN_PROGRESS", "PAUSED", "COMPLETED", "FAILED", "EXPIRED"
    ]
    turn: TranscriptTurn | None = Field(
        default=None, description="이번에 채점된 턴. 물을 것이 없었으면 null",
    )
    cursor: Cursor | None = Field(
        default=None, description="다음에 물을 자리. 그대로 다음 요청에 실어 보낸다. 끝났으면 null",
    )
    current: Question | None = Field(
        default=None, description="다음 질문과 힌트 텍스트. 끝났으면 null",
    )
    progress: Progress | None = None
    termination_reason: str | None = Field(
        default=None,
        description="**이 턴에서 문제가 끝났을 때만 채워진다.** 진행 중이면 null. "
                    "`COMPLETED_L4`(L1~L4 완주) · `TERMINATED_AT_L1`~`TERMINATED_AT_L4`"
                    "(그 단계에서 힌트 소진 후 미달. L4는 L3까지 통과하고 마지막에서 막힌 경우다). "
                    "DB assessment_problem.termination_reason에 그대로 들어간다 — "
                    "종료 판정은 AI가 소유하므로 백엔드가 커서 변화로 역추론하지 않아도 된다",
    )
    ended_level: AxisCode | None = Field(
        default=None,
        description="문제가 끝난 단계. terminationReason과 짝이고 진행 중이면 null. "
                    "DB assessment_problem.ended_level",
    )
    ai_usage: list[AiUsage] = Field(default_factory=list)
