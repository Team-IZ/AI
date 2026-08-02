""" 문답 세션 진행 + 채점 (jobs.py와 형제).

인메모리 dict — 재시작 시 유실. Spring이 transcript를 영속화하고
restore로 재구성한다(명세 §4.4). 스케일/영속 필요 시 Redis·DB로 이전.

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

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.engines.analysis import grading, scoring
from app.engines.analysis.stages import StageError
from app.schemas.session import (
    AnswerSubmit,
    Progress,
    Question,
    SessionRestore,
    SessionStart,
    SessionView,
    TranscriptTurn,
)


@dataclass
class _Session:
    """서버가 들고 있는 세션 상태(휘발성)."""

    session_id: str
    state: str                              # assessment_session.status 6종
    problems: list[dict[str, Any]]          # 동결된 문제. 질문·힌트가 들어 있다
    problem_index: int = 0                  # 지금 몇 번째 문제인가
    axis_index: int = 0                     # 그 문제의 몇 번째 단계인가
    hints_used: int = 0                     # 이 단계에서 쓴 힌트 수 (0~2)
    time_limit_sec: int = 1200
    transcript: list[TranscriptTurn] = field(default_factory=list)
    # 멱등키 -> 그때 돌려준 SessionView. 같은 답변 재전송 시 그대로 반환.
    answered: dict[str, SessionView] = field(default_factory=dict)
    usages: list[dict[str, Any]] = field(default_factory=list)


# session_id -> 세션 상태
_sessions: dict[str, _Session] = {}


def _pick_problems(req_problems: list[Any], selected: list[str]) -> list[dict[str, Any]]:
    """요청이 준 문제 중 이 세션에서 쓸 것만, 준 순서대로.

    **여기서 문제를 만들지 않는다.** 비어 있으면 빈 채로 둔다 — 지어내면 학생이
    분석과 무관한 질문을 받고, 그건 "코드 파편이 곧 근거"라는 전제를 깬다.
    """
    problems = [p if isinstance(p, dict) else p.model_dump() for p in req_problems]
    if selected:
        wanted = set(selected)
        problems = [p for p in problems if p.get("problem_id") in wanted]
    return problems


def _current_stage(sess: _Session) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(문제, 단계). 세션이 끝났으면 None."""
    if sess.problem_index >= len(sess.problems):
        return None
    problem = sess.problems[sess.problem_index]
    stages_ = problem.get("stages") or []
    if sess.axis_index >= len(stages_):
        return None
    return problem, stages_[sess.axis_index]


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


def _to_question(sess: _Session) -> Question | None:
    current = _current_stage(sess)
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
        "sequence_no": sess.problem_index + 1,
        "question_text": stage.get("question_text") or "",
        "code_context": code_context,
        "hint_text": _hint_text(stage, sess.hints_used),
        "hints_used": sess.hints_used,
    })


def _to_view(sess: _Session) -> SessionView:
    current = _to_question(sess) if sess.state == "IN_PROGRESS" else None
    progress = None
    if sess.state == "IN_PROGRESS":
        progress = Progress(problem_index=sess.problem_index + 1,
                            problem_total=len(sess.problems))

    return SessionView(
        session_id=sess.session_id, state=sess.state,
        current=current, progress=progress, transcript=sess.transcript,
    )


def _advance_problem(sess: _Session) -> None:
    """이 문제를 닫고 다음 문제의 L1로. 남은 문제가 없으면 세션 종료."""
    sess.problem_index += 1
    sess.axis_index = 0
    sess.hints_used = 0
    if sess.problem_index >= len(sess.problems):
        sess.state = "COMPLETED"


def start_session(req: SessionStart) -> SessionView:
    """세션 만들고 첫 질문 돌려줌."""
    sid = req.session_id or str(uuid.uuid4())
    problems = _pick_problems(req.problems, req.selected_problem_ids)
    sess = _Session(
        session_id=sid,
        state="IN_PROGRESS" if problems else "COMPLETED",
        problems=problems,
        time_limit_sec=req.time_limit_sec,
    )
    _sessions[sid] = sess
    return _to_view(sess)


def get_session(session_id: str) -> SessionView | None:
    sess = _sessions.get(session_id)
    return _to_view(sess) if sess else None


def submit_answer(session_id: str, req: AnswerSubmit) -> SessionView | None:
    """답변을 채점하고 다음에 무엇을 물을지 정한다.

    **세션에서 유일한 LLM 호출이 여기다.** 실패하면 그 턴을 버리지 않고 예외를
    올린다 — 라우터가 502로 돌려주면 프론트가 재전송할 수 있고, 멱등키가 같으므로
    중복 턴이 되지 않는다. 0점으로 기록하면 학생이 억울하게 깎인다.
    """
    sess = _sessions.get(session_id)
    if sess is None:
        return None     # 라우터가 404로 변환

    # 멱등: 같은 client_request_id면 처음 돌려준 응답을 그대로 반환(중복 턴 방지)
    if req.client_request_id in sess.answered:
        return sess.answered[req.client_request_id]

    current = _current_stage(sess)
    if current is None:
        return _to_view(sess)          # 이미 끝난 세션. 조용히 현재 상태를 돌려준다
    problem, stage = current

    settings = get_settings()
    axis_code = stage.get("axis_code")
    question_text = stage.get("question_text") or ""

    # 지금까지 이 단계에서 보여준 힌트 전부. 길이가 곧 hintsUsed이고,
    # 그게 점수 상한(5/4/3)과 자력 판정을 정한다.
    shown_hints = [h for h in (_hint_text(stage, i) for i in range(1, sess.hints_used + 1))
                   if h]

    grade = grading.grade(
        axis_code, question_text, req.answer_text,
        model_code=settings.model_code_session,
        hints=shown_hints,
        code_snippet=problem.get("code_snippet") or "",
        code_ref=problem.get("source_path") or "",
    )
    sess.usages.extend({**u, "feature_code": "GRADING"} for u in grade.usages)

    sess.transcript.append(
        TranscriptTurn(
            problem_id=problem.get("problem_id", ""),
            axis_code=axis_code,
            question_text=question_text,
            answer_text=req.answer_text,
            answered_at=datetime.now(timezone.utc).isoformat(),
            best_score=grade.best_score,
            confirmed_score=grade.confirmed_score,
            attempt_count=sess.hints_used + 1,
            hint_text=_hint_text(stage, sess.hints_used),
            autonomy=grade.autonomy,
        )
    )

    if grade.passed:
        # 다음 단계로. L4까지 통과했으면 이 문제는 완주다.
        sess.axis_index += 1
        sess.hints_used = 0
        if sess.axis_index >= len(problem.get("stages") or []):
            _advance_problem(sess)
    elif sess.hints_used < scoring.MAX_HINTS_PER_LEVEL:
        # 힌트를 하나 더 열고 같은 단계를 다시 묻는다. 질문은 안 바뀐다.
        sess.hints_used += 1
    else:
        # 힌트 소진 후에도 미달 — 그 문제는 여기서 끝이다. 다음 단계를 던지지 않는다.
        _advance_problem(sess)

    view = _to_view(sess)
    sess.answered[req.client_request_id] = view     # 멱등 재전송 대비 저장
    return view


def restore_session(session_id: str, req: SessionRestore) -> SessionView:
    """Spring이 저장해둔 transcript로 유실 세션 재구성, 이어질 질문 반환.

    **transcript를 되짚어 위치를 복원한다.** 턴 수만 세면 안 된다 — 힌트 후 재질의도
    한 턴이라, 같은 단계에서 세 턴이 나올 수 있고 그러면 커서가 세 칸 밀린다.
    """
    problems = _pick_problems(req.problems, [])
    sess = _Session(
        session_id=session_id, state="IN_PROGRESS", problems=problems,
        time_limit_sec=req.time_limit_sec,
    )
    sess.transcript.extend(req.transcript)

    # 확정된 턴을 그대로 재생해 커서를 옮긴다. 판정 규칙이 submit_answer와 한 벌이라
    # 여기서 다시 쓰지 않고 같은 값(scoring)을 본다.
    for turn in req.transcript:
        current = _current_stage(sess)
        if current is None:
            break
        _, stage = current
        if turn.confirmed_score >= scoring.PASS_SCORE:
            sess.axis_index += 1
            sess.hints_used = 0
            if sess.axis_index >= len(sess.problems[sess.problem_index].get("stages") or []):
                _advance_problem(sess)
        elif sess.hints_used < scoring.MAX_HINTS_PER_LEVEL:
            sess.hints_used += 1
        else:
            _advance_problem(sess)

    _sessions[session_id] = sess
    return _to_view(sess)
