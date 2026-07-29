""" 문답 세션의 인메모리 저장소 + 스텁 진행 로직 (jobs.py와 형제).

인메모리 dict — 재시작 시 유실. Spring이 transcript를 영속화하고
restore로 재구성한다(명세 §4.4). 스케일/영속 필요 시 Redis·DB로 이전.

스텁이라 질문은 고정 스크립트다. 실제 소크라틱 문답 엔진은 9단계에서 이식(P03).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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
class _Turn:
    """ 내부 저장용 턴. 와이어 모델(TranscriptTurn)과 분리해 둔다. """
    problem_id: str
    axis_code: str
    question_text: str
    answer_text: str
    answered_at: str
    
@dataclass
class _Session:
    """ 서버가 들고 있는 세션 상태(휘발성) """
    session_id: str
    state: str                          # IN_PROGRESS | COMPLETED | TIMEOUT | FAILED
    questions: list[dict[str, Any]]      # 고정 질문 스크립트
    cursor: int                         # 현재 질문 인덱스
    time_limit_sec: int
    transcript: list[_Turn] = field(default_factory=list)
    # 멱등키 -> 그때 돌려준 SessionView. 같은 답변 재전송 시 그대로 반환.
    answered: dict[str, SessionView] = field(default_factory=dict)
    
# session_id -> 세션 상태
_sessions: dict[str, _Session] = {}

def _build_questions(problem_ids: list[str]) -> list[dict[str, Any]]:
    """스텁 질문 스크립트. 문제당 질문 하나. 문제가 없으면 기본 2개."""
    ids = problem_ids or ["prob-stub-1", "prob-stub-2"]
    return [
        {
            "problem_id": problem_id,
            "axis_code": "L1_CODE_DESCRIPTION",
            "sequence_no": i,
            "question_text": f"[stub] {problem_id}에 대한 당신의 의도를 설명해 주세요.",
            "code_context": {"path": "app/main.py", "snippet": "...", "line_start": 1},
        }
        for i, problem_id in enumerate(ids, start=1)
    ]
    
def _to_view(sess: _Session) -> SessionView:
    """ 내부 상태를 와이어 응답으로 변환 """
    current = None
    progress = None
    if sess.state == "IN_PROGRESS":
        current = Question.model_validate(sess.questions[sess.cursor])
        progress = Progress(problem_index=sess.cursor + 1, problem_total=len(sess.questions))

    transcript = [
        TranscriptTurn(
            problem_id=t.problem_id,
            axis_code=t.axis_code,
            question_text=t.question_text,
            answer_text=t.answer_text,
            answered_at=t.answered_at,
        )
        for t in sess.transcript
    ]
    return SessionView(
        session_id=sess.session_id, state=sess.state,
        current=current, progress=progress, transcript=transcript,
    )
    
def start_session(req: SessionStart) -> SessionView:
    """ 세션 만들고 첫 질문 돌려줌 """
    sid = req.session_id or str(uuid.uuid4())
    sess = _Session(
        session_id=sid, state="IN_PROGRESS",
        questions=_build_questions(req.selected_problem_ids),
        cursor=0, time_limit_sec=req.time_limit_sec,
    )
    _sessions[sid] = sess
    return _to_view(sess)

def get_session(session_id: str) -> SessionView | None:
    sess  = _sessions.get(session_id)
    return _to_view(sess) if sess else None

def submit_answer(session_id: str, req: AnswerSubmit) -> SessionView | None:
    """ 답변 받아 transcript에 확정, 다음 질문 또는 종류 돌려줌 """
    sess = _sessions.get(session_id)
    if sess is None:
        return None     # 라우터가 404로 변환
    
    # 멱등: 같은 client_request_id면 처음 돌려준 응답을 그대로 반환(중복 턴 방지)
    if req.client_request_id in sess.answered:
        return sess.answered[req.client_request_id]
    
    # 현재 질문에 대한 답을 확정 턴으로 기록
    q = sess.questions[sess.cursor]
    sess.transcript.append(
        _Turn(
            problem_id=q["problem_id"], axis_code=q["axis_code"],
            question_text=q["question_text"], answer_text=req.answer_text,
            answered_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    sess.cursor += 1
    if sess.cursor >= len(sess.questions):
        sess.state = "COMPLETED"
        # time_limit_sec 기반 timeout 판정은 스텁에서 생략
        # 실제 시계 필요한 로직이라 9단계 실물 엔진에서 붙임
    
    view = _to_view(sess)
    sess.answered[req.client_request_id] = view     # 멱등 재전송 대비 저장
    return view

def restore_session(session_id: str, req: SessionRestore) -> SessionView:
    """ Spring이 저장해둔 transcript로 유실 세션 재구성, 이어질 질문 반환
    
    완료된 턴 수만큼 cursor를 밀어 그 다음 질문부터 이어나감
    """
    problem_ids = [p.get("problem_id", "") for p in req.problems]
    sess = _Session(
        session_id=session_id, state="IN_PROGRESS",
        questions=_build_questions([i for i in problem_ids if i]),
        cursor=len(req.transcript), time_limit_sec=req.time_limit_sec,
    )
    for t in req.transcript:
        sess.transcript.append(
            _Turn(
                problem_id=t.problem_id, axis_code=t.axis_code,
                question_text=t.question_text, answer_text=t.answer_text,
                answered_at=t.answered_at,
            )
        )
    if sess.cursor >= len(sess.questions):
        sess.state = "COMPLETED"
    _sessions[session_id] = sess
    return _to_view(sess)

