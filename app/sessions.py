""" 문답 세션 진행 + 채점 (jobs.py와 형제).

🔴 **무상태다** (2026-08-03, PLAN §T11). AI는 세션을 들고 있지 않는다 — 문제·기록·커서가
매 요청에 실려 오고, 여기서는 그것을 읽어 채점하고 **다음 커서를 계산해 돌려준다.**
남는 상태는 멱등 재전송 방어용 캐시 하나뿐이고, 그건 날아가도 중복 채점 한 번으로 끝난다.
그래서 배포·재시작·EC2 Stop이 진행 중인 세션을 깨지 않는다.

🔴 **AI는 세션 중에 아무것도 만들지 않는다** (2026-08-02 전면 동결, PLAN §T10).
문제·질문·힌트는 분석 배치에서 동결돼 요청에 실려 온다. 여기서 도는 LLM 호출은
**채점(p04-5) 하나뿐**이다.

## 계단 규칙 (scoring.py가 값의 출처)

    L1 → L2 → L3 → L4 순서로 오른다. 건너뛰지 않는다.
    통과선 3점. 미달이면 힌트를 하나 열고 같은 단계를 다시 묻는다.
    힌트는 단계당 2개. 소진 후에도 미달이면 **그 문제는 거기서 끝**이고
    남은 단계는 미도달로 남는다. 다음 문제의 L1로 간다.

**힌트를 열어도 질문은 안 바뀐다.** 힌트는 재진술이라 원 질문을 대체하지 않는다.
그래서 `Question.questionText`는 그대로고 `hintText`만 붙는다.
"""

import hashlib
import hmac
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, get_args

from app.api.errors import ApiError
from app.config import get_settings
from app.engines.analysis import grading, scoring
from app.schemas.report import AxisCode
from app.schemas.session import (
    AnswerResult,
    AnswerSubmit,
    Cursor,
    Progress,
    Question,
    TranscriptTurn,
)
from app.usage import to_ai_usage

_AXES = list(get_args(AxisCode))

# 멱등: "{session_id}:{client_request_id}" -> 그때 돌려준 응답.
# ponytail: 상한 있는 dict. 재시작하면 비지만 그때 잃는 것은 "재전송 한 번이
# 중복 채점이 된다"뿐이다(세션 진행은 요청이 들고 온다). Redis는 그게 실제로
# 문제가 될 때.
#
# M8 (redteam audit, 2026-08-05): 이 키가 안전하려면 **session_id가 프로세스
# 수명 동안 전역 유일**해야 한다 -- 계약이지 코드가 강제하는 것이 아니다.
#   WHY: 서로 다른 두 학생 세션이 같은 session_id를 쓰면(Spring이 재사용하거나,
#   초기화 로직이 이전 세션과 같은 id를 다시 발급하면) 뒤 세션의 client_request_id가
#   앞 세션의 것과 우연히 같을 때 앞 세션의 채점 응답을 그대로 받아간다 --
#   tests/test_sessions.py의 Backend 헬퍼가 이미 이 현상을 주석으로 관측하고
#   `uuid.uuid4()`로 회피하고 있다("멱등 캐시가 프로세스 전역이라 세션 id가
#   겹치면 앞 테스트의 응답이 돌아온다").
#   COST: 근본 수정(예: problems/transcript 해시를 키에 같이 접어 넣어 session_id
#   충돌이 나도 문제 내용까지 같아야 충돌하게 만드는 것)은 여기서 하지 않는다 --
#   이 서비스는 의도적으로 무상태다(파일 상단 🔴). session_id의 유일성은 그래서
#   **Spring 쪽 계약 사항**으로 남긴다: session_id는 절대 재사용/재발급하지 않아야
#   한다.
#   EXIT: 실제로 session_id 충돌이 관측되면(로그·리포트로), H10의 _compute_cursor_mac
#   과 같은 해시-접기 패턴을 이 키에도 적용 -- 그 전까지는 이 계약 위반이 실제로
#   일어나지 않는다는 전제 위에서만 안전하다.
_ANSWERED_MAX = 2000
_answered: "OrderedDict[str, AnswerResult]" = OrderedDict()


def _remember(key: str, view: AnswerResult) -> AnswerResult:
    _answered[key] = view
    while len(_answered) > _ANSWERED_MAX:
        _answered.popitem(last=False)
    return view


@dataclass
class _Walk:
    """요청 payload로 재구성한 진행 상태. 요청 하나 동안만 산다."""

    problems: list[dict[str, Any]]
    problem_index: int = 0                  # 지금 몇 번째 문제인가
    axis_index: int = 0                     # 그 문제의 몇 번째 단계인가
    hints_used: int = 0                     # 이 단계에서 쓴 힌트 수 (0~2)
    usages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.problem_index >= len(self.problems)


def _current_stage(walk: _Walk) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(문제, 단계). 다 끝났으면 None."""
    if walk.done:
        return None
    problem = walk.problems[walk.problem_index]
    stages_ = problem.get("stages") or []
    if walk.axis_index >= len(stages_):
        return None
    return problem, stages_[walk.axis_index]


# 채점에 넣을 근거 코드의 앞뒤 여유 줄. 파편만 주면 모델이 "이 메서드가 어디 소속인지"를
# 못 보고, 넉넉히 주면 매니페스트의 code_block 상한(4,000자)에 걸려 뒤가 잘린다.
_GRADING_CONTEXT_LINES = 8


def _grading_code(problem: dict[str, Any]) -> str:
    """채점 프롬프트에 넣을 근거 코드.

    🔴 **`codeSnippet`을 그대로 넣으면 안 된다.** 2026-08-03부터 그 값은 **파일 전체**다
    (학생 화면용). 채점 프롬프트의 `code_block` 상한이 4,000자라 큰 파일은 앞에서부터
    잘리고, 문제 구간이 파일 뒤쪽이면 **근거가 통째로 사라진 채 채점된다** — 에러 없이
    점수만 틀린다. 그래서 여기서 `lineStart`/`lineEnd`로 되잘라 낸다.
    """
    text = problem.get("code_snippet") or ""
    start = problem.get("line_start") or 1
    end = problem.get("line_end") or start
    lines = text.splitlines()
    # 파편이 그대로 온 경우(파일을 못 찾았거나 너무 커서 되돌린 경우)엔 줄 번호가
    # 이 문자열의 색인이 아니다. 줄 수로 판별해 그때는 원문을 그대로 쓴다.
    if len(lines) < end:
        return text
    lo = max(0, start - 1 - _GRADING_CONTEXT_LINES)
    hi = min(len(lines), end + _GRADING_CONTEXT_LINES)
    return "\n".join(lines[lo:hi])


def _hint_text(stage: dict[str, Any], hints_used: int) -> str | None:
    """지금 보여줄 힌트. `hints_used`가 0이면 아직 없다.

    **순서가 곧 레벨이다** — `hints[hintsUsed - 1]`로 꺼내므로 [1, 2] 순서가
    보장돼야 한다(ProblemStage 검증기가 강제한다).
    """
    if hints_used <= 0:
        return None
    hint_list = stage.get("hints") or []
    if hints_used > len(hint_list):
        return None
    hint = hint_list[hints_used - 1]
    return hint.get("hint_text") if isinstance(hint, dict) else getattr(hint, "hint_text", None)


def _to_question(walk: _Walk) -> Question | None:
    current = _current_stage(walk)
    if current is None:
        return None
    problem, stage = current

    code_context = None
    if problem.get("source_path"):
        code_context = {
            "path": problem["source_path"],
            "snippet": problem.get("code_snippet") or "",
            "line_start": problem.get("line_start") or 1,
        }

    return Question.model_validate({
        "problem_id": problem.get("problem_id", ""),
        "axis_code": stage.get("axis_code"),
        # 화면의 "문제 2 / 3"이 이 값으로 그려진다. 단계는 세지 않는다 —
        # 학생마다 어디까지 가는지 달라 "3/4단계"는 거짓 진행률이 된다.
        "sequence_no": walk.problem_index + 1,
        "question_text": stage.get("question_text") or "",
        "code_context": code_context,
        "hint_text": _hint_text(stage, walk.hints_used),
        "hints_used": walk.hints_used,
    })


# D-fix (redteam audit H10, 2026-08-04): 무상태 세션의 무결성 보호.
#
# _seek()은 원래 "커서가 오면 그대로 믿는다"였다 -- 클라이언트가 problem_id/axis_code/
# hints_used를 위조해 정답을 맞춘 적 없이 완주 상태를 만들 수 있었다. 그런데 cursor만
# 지키는 건 부분적이다 -- AnswerSubmit은 problems(문제·힌트 전체)와 transcript(턴
# 기록 전체)도 매 요청에 실려오고, 이 둘도 cursor와 똑같이 무검증으로 쓰인다
# (2026-08-04 교차검증 지적). 그래서 mac은 cursor 필드뿐 아니라 problems/transcript의
# 해시까지 서명 입력에 포함한다 -- 셋 중 하나만 바뀌어도 불일치가 난다.
#
#   WHY: 이건 "완전한 수정"이 아니라 Spring이 이 필드를 받아들이기 시작하기 전까지의
#   발판이다. session_cursor_hmac_secret이 비어 있으면(로컬 개발, 또는 Spring이 아직
#   이 필드를 왕복시키지 않는 배포 초기) mac 발급·검증을 통째로 건너뛰어 오늘과 동일하게
#   동작한다 -- 하위호환.
#   COST: 클라이언트가 그냥 mac 필드를 아예 안 보내면 여전히 뚫린다(의도된 설계 —
#   강제가 아니라 마이그레이션 경로). 유일한 호출자가 INTERNAL_API_KEY로 인증된
#   Spring이라는 전제 위에서만 위험이 제한적이다 -- 그 전제가 깨지면(예: 다른 진입점이
#   생기거나 Spring이 학생 입력을 그대로 중계) 즉시 재평가 필요.
#   EXIT: Spring이 mac을 항상 보내는 것이 확인되면, expected is None(secret 미설정)
#   허용 분기를 지우고 secret 미설정 자체를 기동 거부 조건으로 승격.
def _canonical_hash(value: Any) -> str:
    """problems/transcript를 결정론적으로 직렬화해 해시한다 -- 발급·검증 양쪽이 같은
    입력에 항상 같은 해시를 내야 한다(dict 키 순서에 안 흔들리게 sort_keys)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _compute_cursor_mac(
    session_id: str, problem_id: str, axis_code: str, hints_used: int,
    problems: list[dict[str, Any]], transcript: list[dict[str, Any]],
) -> str | None:
    """secret 미설정이면 None -- 호출부가 "발급 스킵"/"검증 스킵" 신호로 그대로 쓴다."""
    secret = get_settings().session_cursor_hmac_secret
    if not secret:
        return None
    payload = "|".join([
        session_id, problem_id, axis_code, str(hints_used),
        _canonical_hash(problems), _canonical_hash(transcript),
    ])
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _to_cursor(walk: _Walk, session_id: str, transcript_so_far: list[dict[str, Any]]) -> Cursor | None:
    """다음에 물을 자리. 백엔드가 저장했다가 다음 요청에 그대로 실어 보낸다."""
    current = _current_stage(walk)
    if current is None:
        return None
    problem, stage = current
    problem_id = problem.get("problem_id", "")
    axis_code = stage.get("axis_code")
    return Cursor(
        problem_id=problem_id,
        axis_code=axis_code,
        hints_used=walk.hints_used,
        mac=_compute_cursor_mac(session_id, problem_id, axis_code, walk.hints_used, walk.problems, transcript_so_far),
    )


def _advance_problem(walk: _Walk) -> None:
    """이 문제를 닫고 다음 문제의 L1로."""
    walk.problem_index += 1
    walk.axis_index = 0
    walk.hints_used = 0


def _advance(walk: _Walk, passed: bool) -> tuple[str, str] | None:
    """계단 규칙. **진행 규칙의 단일 출처다** — 백엔드에 같은 규칙을 두지 않는다.

    문제가 이 턴에서 끝났으면 `(terminationReason, endedLevel)`을 돌려준다.
    **종료 판정을 하는 자리가 곧 그 사유를 아는 자리다** — 응답에 안 실으면 백엔드가
    커서가 다음 문제로 넘어간 것을 보고 "왜 끝났는지"를 역추론해야 한다.
    """
    axis = _AXES[walk.axis_index]
    if passed:
        # 다음 단계로. L4까지 통과했으면 이 문제는 완주다.
        walk.axis_index += 1
        walk.hints_used = 0
        stages_ = walk.problems[walk.problem_index].get("stages") or []
        if walk.axis_index >= len(stages_):
            _advance_problem(walk)
            return "COMPLETED_L4", axis
        return None
    if walk.hints_used < scoring.MAX_HINTS_PER_LEVEL:
        # 힌트를 하나 더 열고 같은 단계를 다시 묻는다. 질문은 안 바뀐다.
        walk.hints_used += 1
        return None
    # 힌트 소진 후에도 미달 — 그 문제는 여기서 끝이다. 다음 단계를 던지지 않는다.
    _advance_problem(walk)
    return f"TERMINATED_AT_{axis}", axis


def _seek(walk: _Walk, session_id: str, cursor: Cursor | None,
          transcript: list[TranscriptTurn], transcript_dicts: list[dict[str, Any]]) -> None:
    """요청이 준 위치로 커서를 옮긴다. **매 요청이 곧 restore다.**

    커서가 오면 그대로 믿는다(백엔드 장부가 원본이다) -- 단, mac이 같이 왔고 우리가
    검증할 수 있으면(secret 설정됨) 먼저 대조한다(H10). 없으면 transcript를 되짚는다 —
    턴 수만 세면 안 된다. 힌트 후 재질의도 한 턴이라 같은 단계에서 세 턴이 나올 수 있고,
    그러면 커서가 세 칸 밀린다.
    """
    if cursor is not None:
        if cursor.mac is not None:
            expected = _compute_cursor_mac(
                session_id, cursor.problem_id, cursor.axis_code, cursor.hints_used,
                walk.problems, transcript_dicts,
            )
            # expected is None => secret 미설정, 검증 자체가 불가능 -- 하위호환으로
            # 그대로 신뢰(오늘까지의 동작). secret이 있는데 불일치하면 위조로 간주.
            if expected is not None and not hmac.compare_digest(cursor.mac, expected):
                raise ApiError(
                    status_code=400,
                    error="CURSOR_INTEGRITY_MISMATCH",
                    message="cursor.mac이 problems/transcript와 일치하지 않습니다 -- "
                            "커서·문제·기록 중 하나가 응답으로 발급됐을 때와 달라졌습니다.",
                    retryable=False,
                )
        ids = [p.get("problem_id") for p in walk.problems]
        if cursor.problem_id in ids:
            walk.problem_index = ids.index(cursor.problem_id)
        walk.axis_index = _AXES.index(cursor.axis_code)
        walk.hints_used = cursor.hints_used
        return

    for turn in transcript:
        if _current_stage(walk) is None:
            break
        _advance(walk, turn.passed)


def _to_result(session_id: str, walk: _Walk, turn: TranscriptTurn | None,
               trace_id: str | None, transcript_so_far: list[dict[str, Any]],
               ended: tuple[str, str] | None = None,
               request_key: str | None = None) -> AnswerResult:
    completed = _current_stage(walk) is None
    return AnswerResult(
        session_id=session_id,
        state="COMPLETED" if completed else "IN_PROGRESS",
        turn=turn,
        cursor=_to_cursor(walk, session_id, transcript_so_far),
        current=_to_question(walk),
        progress=None if completed else Progress(
            problem_index=walk.problem_index + 1, problem_total=len(walk.problems),
        ),
        termination_reason=ended[0] if ended else None,
        ended_level=ended[1] if ended else None,
        # 🔴 멱등키에 **요청 단위 키(clientRequestId)**를 넣는다. 세션 id만 쓰면
        # 순번이 요청마다 1부터 다시 시작해 턴끼리 키가 겹치고, DB에서
        # ai_usage.idempotency_key가 전역 UNIQUE라 두 번째 턴부터 원장이 통째로
        # 거부된다(실측: 11턴 37행 중 서로 다른 키가 5개뿐이었다).
        ai_usage=to_ai_usage(walk.usages, "ASSESSMENT_SESSION", session_id,
                             feature_code="ANSWER_EVALUATION",
                             idempotency_key=request_key or session_id,
                             trace_id=trace_id),
    )


def submit_answer(session_id: str, req: AnswerSubmit,
                  *, trace_id: str | None = None) -> AnswerResult:
    """답변을 채점하고 다음에 무엇을 물을지 정한다.

    **세션에서 유일한 LLM 호출이 여기다.** 실패하면 그 턴을 버리지 않고 예외를
    올린다 — 라우터가 503 GRADING_UNAVAILABLE로 돌려주면 프론트가 재전송할 수 있고,
    멱등키가 같으므로 중복 턴이 되지 않는다. 0점으로 기록하면 학생이 억울하게 깎인다.
    """
    key = f"{session_id}:{req.client_request_id}"
    if key in _answered:
        return _answered[key]           # 같은 답변 재전송 → 처음 응답 그대로

    # **여기서 문제를 만들지 않는다.** 안 주면 물을 것이 없다 — 지어내면 학생이
    # 분석과 무관한 질문을 받고, 그건 "코드 파편이 곧 근거"라는 전제를 깬다.
    walk = _Walk(problems=[p.model_dump() for p in req.problems])
    # H10: mac 발급·검증 양쪽에 쓰는 transcript의 정규화된 사본. 응답에 새 커서를
    # 실을 때는 이 턴이 끝난 뒤(아래에서) 새 turn을 이 리스트에 덧붙인 버전으로 다시 쓴다.
    transcript_dicts = [t.model_dump(mode="json") for t in req.transcript]
    _seek(walk, session_id, req.cursor, req.transcript, transcript_dicts)

    current = _current_stage(walk)
    if current is None:
        # 이미 끝난 세션. 조용히 끝난 상태를 돌려준다(채점할 것이 없다).
        return _to_result(session_id, walk, None, trace_id, transcript_dicts,
                          request_key=f"{session_id}:{req.client_request_id}")
    problem, stage = current

    axis_code = stage.get("axis_code")
    question_text = stage.get("question_text") or ""

    # 지금까지 이 단계에서 보여준 힌트 전부. 길이가 곧 hintsUsed이고,
    # 그게 점수 상한(5/4/3)과 자력 판정을 정한다.
    shown_hints = [h for h in (_hint_text(stage, i) for i in range(1, walk.hints_used + 1))
                   if h]

    grade = grading.grade(
        axis_code, question_text, req.answer_text,
        # 요청이 이기고 없으면 서버 기본값. 채점 모델은 operator가 고른다(GradingPolicy).
        model_code=req.provider_model_code or get_settings().model_code_session,
        hints=shown_hints,
        code_snippet=_grading_code(problem),
        code_ref=problem.get("source_path") or "",
        analysis_context=req.analysis_context,
    )
    walk.usages.extend(grade.usages)

    turn = TranscriptTurn(
        problem_id=problem.get("problem_id", ""),
        axis_code=axis_code,
        question_text=question_text,
        answer_text=req.answer_text,
        answered_at=datetime.now(timezone.utc).isoformat(),
        score=grade.score,
        passed=grade.passed,
        hints_used=walk.hints_used,
        hint_text=_hint_text(stage, walk.hints_used),
    )

    ended = _advance(walk, grade.passed)
    # H10: 다음 커서의 mac은 "이 턴까지 끝난" transcript를 서명해야 한다 -- 응답이
    # transcript 전체를 안 돌려주므로(AnswerResult 자체 계약), 다음 요청에서 클라이언트가
    # 이 turn을 자기 쪽 transcript에 이어붙여 그대로 되돌려 보내는 것이 전제다.
    next_transcript = transcript_dicts + [turn.model_dump(mode="json")]
    return _remember(key, _to_result(session_id, walk, turn, trace_id, next_transcript, ended,
                                     request_key=f"{session_id}:{req.client_request_id}"))
