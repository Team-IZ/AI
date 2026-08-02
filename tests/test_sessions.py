""" 문답 세션 진행 + 채점.

**AI는 세션 중에 아무것도 만들지 않는다** — 문제·질문·힌트는 요청에 실려 오고
LLM 호출은 채점 하나뿐이다. 그래서 여기 테스트는 **계단 규칙**을 잰다:
언제 다음 단계로 가고, 언제 힌트가 열리고, 언제 문제가 끝나는가.

채점기는 가짜다. 재는 것은 모델 품질이 아니라 판정에 따른 **진행 규칙**이다.
"""
import pytest
from fastapi.testclient import TestClient

from app import sessions as sessions_mod
from app.config import get_settings
from app.engines.analysis import grading
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}


def _stage(axis: str) -> dict:
    return {
        "axisCode": axis,
        "questionText": f"{axis} 질문",
        "hints": [
            {"hintLevel": 1, "hintText": f"{axis} 힌트 1"},
            {"hintLevel": 2, "hintText": f"{axis} 힌트 2"},
        ],
    }


def _problem(no: int) -> dict:
    return {
        "problemId": f"prob-{no}", "problemNo": no, "problemType": "DESIGN_CHOICE",
        "priority": 1.0, "sourcePath": "app/pay.py", "lineStart": 4, "lineEnd": 7,
        "codeSnippet": "def pay(order, method):", "evidenceHash": "a" * 64,
        "extractorVersion": "v0",
        "stages": [_stage(a) for a in ("L1", "L2", "L3", "L4")],
    }


START_BODY = {"attemptId": "a-1", "timeLimitSec": 1200,
              "problems": [_problem(1), _problem(2)]}


@pytest.fixture
def score(monkeypatch):
    """채점 점수를 시나리오로 준다. 다 쓰면 마지막 값을 반복한다."""
    plan: list[int] = [4]

    def _grade(axis_code, question, answer, *, model_code, hints=None, **kw):
        value = plan[0] if len(plan) == 1 else plan.pop(0)
        used = len(hints or [])
        confirmed = min(value, grading.scoring.cap_for(used))
        return grading.Grade(
            axis_code=axis_code, best_score=value, confirmed_score=confirmed,
            hints_used=used, passed=confirmed >= grading.scoring.PASS_SCORE,
            autonomy=grading.scoring.autonomy_for(used),
            matched_level="", evidence="", missing="", usages=[],
        )

    monkeypatch.setattr(sessions_mod.grading, "grade", _grade)
    return plan


def _start() -> str:
    r = client.post("/api/v0/sessions", json=START_BODY, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["sessionId"]


def _answer(sid: str, request_id: str, text: str = "답변입니다") -> dict:
    return client.post(f"/api/v0/sessions/{sid}/answers",
                       json={"clientRequestId": request_id, "answerText": text},
                       headers=HEADERS).json()


def test_start_serves_the_frozen_first_question(score):
    """세션 시작 → 분석이 동결해 둔 L1 질문이 그대로 나온다. 만들지 않는다."""
    body = client.post("/api/v0/sessions", json=START_BODY, headers=HEADERS).json()

    assert body["state"] == "IN_PROGRESS"
    assert body["current"]["axisCode"] == "L1"
    assert body["current"]["questionText"] == "L1 질문"
    assert body["current"]["hintText"] is None        # 첫 시도엔 힌트 없음
    assert body["current"]["codeContext"]["path"] == "app/pay.py"
    assert body["progress"]["problemTotal"] == 2


def test_no_problems_means_no_session(score):
    """문제를 안 주면 물을 것이 없다. 지어내지 않는다 — 그게 근거 전제를 깬다."""
    body = client.post("/api/v0/sessions", json={"timeLimitSec": 1200},
                       headers=HEADERS).json()

    assert body["state"] == "COMPLETED"
    assert body["current"] is None


def test_pass_climbs_to_the_next_axis(score):
    """통과하면 다음 단계로. 계단은 건너뛰지 않는다."""
    sid = _start()

    body = _answer(sid, "r1")

    assert body["current"]["axisCode"] == "L2"
    assert body["current"]["hintsUsed"] == 0


def test_fail_opens_a_hint_and_keeps_the_question(score):
    """미달이면 힌트가 열리고 **같은 질문을 다시 묻는다**.

    힌트는 재진술이라 원 질문을 대체하지 않는다 — questionText가 그대로여야 한다.
    """
    score[:] = [2]
    sid = _start()

    body = _answer(sid, "r1")

    assert body["current"]["axisCode"] == "L1"           # 같은 단계
    assert body["current"]["questionText"] == "L1 질문"   # 같은 질문
    assert body["current"]["hintText"] == "L1 힌트 1"
    assert body["current"]["hintsUsed"] == 1


def test_second_failure_opens_the_second_hint(score):
    score[:] = [2]
    sid = _start()
    _answer(sid, "r1")

    body = _answer(sid, "r2")

    assert body["current"]["hintText"] == "L1 힌트 2"
    assert body["current"]["hintsUsed"] == 2


def test_exhausted_hints_end_the_problem(score):
    """힌트를 다 쓰고도 미달이면 그 문제는 끝이다. 다음 단계를 던지지 않는다."""
    score[:] = [2]
    sid = _start()
    _answer(sid, "r1")
    _answer(sid, "r2")

    body = _answer(sid, "r3")

    assert body["current"]["problemId"] == "prob-2"      # 다음 문제로
    assert body["current"]["axisCode"] == "L1"           # 그 문제의 L1부터
    assert len(body["transcript"]) == 3                  # 세 시도가 다 기록된다


def test_hint_cap_is_recorded_in_the_turn(score):
    """힌트를 쓰고 통과하면 원점수는 보존되고 기록 점수만 상한에 걸린다."""
    score[:] = [2, 5]
    sid = _start()
    _answer(sid, "r1")                                   # 미달 → 힌트 1

    turn = _answer(sid, "r2")["transcript"][1]

    assert turn["bestScore"] == 5
    assert turn["confirmedScore"] == 4                   # 힌트 1회 상한
    assert turn["autonomy"] == "SELF_MAINTAINED"
    assert turn["hintText"] == "L1 힌트 1"               # 어떤 힌트를 보고 답했는지
    assert turn["attemptCount"] == 2


def test_completing_all_axes_moves_to_the_next_problem(score):
    """L4까지 통과하면 그 문제는 완주. 다음 문제의 L1로 간다."""
    sid = _start()
    for i, _ in enumerate(("L1", "L2", "L3", "L4"), start=1):
        body = _answer(sid, f"r{i}")

    assert body["current"]["problemId"] == "prob-2"
    assert body["current"]["axisCode"] == "L1"


def test_session_completes_after_the_last_problem(score):
    """문제 2개 × 4단계 = 8턴이면 끝."""
    sid = _start()
    for i in range(8):
        body = _answer(sid, f"r{i}")

    assert body["state"] == "COMPLETED"
    assert body["current"] is None
    assert len(body["transcript"]) == 8


def test_same_client_request_id_is_idempotent(score):
    """같은 clientRequestId 재전송 → 동일 응답. 커서가 두 번 밀리지 않는다."""
    sid = _start()
    first = _answer(sid, "dup")
    second = _answer(sid, "dup")

    assert first == second
    assert first["current"]["axisCode"] == "L2"


def test_get_session_returns_state(score):
    sid = _start()

    r = client.get(f"/api/v0/sessions/{sid}", headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["sessionId"] == sid
    assert r.json()["state"] == "IN_PROGRESS"


def test_unknown_session_returns_404():
    """모르는 세션 → 404 SESSION_NOT_FOUND, retryable=false."""
    r = client.get("/api/v0/sessions/nope", headers=HEADERS)

    assert r.status_code == 404
    assert r.json()["error"] == "SESSION_NOT_FOUND"
    assert r.json()["retryable"] is False


def test_restore_replays_the_transcript(score):
    """턴 수만 세면 안 된다 — 힌트 후 재질의도 한 턴이라 커서가 밀린다.

    아래는 L1에서 두 번 미달한 기록이다. 커서는 여전히 L1이고 힌트가 2개 열려 있어야 한다.
    """
    turn = {"problemId": "prob-1", "axisCode": "L1", "questionText": "L1 질문",
            "answerText": "a", "answeredAt": "2026-08-02T00:00:00Z",
            "bestScore": 2, "confirmedScore": 2, "attemptCount": 1, "autonomy": "SELF"}

    body = client.post("/api/v0/sessions/restored-1/restore",
                       json={"timeLimitSec": 1200, "transcript": [turn, {**turn, "attemptCount": 2}],
                             "problems": [_problem(1), _problem(2)]},
                       headers=HEADERS).json()

    assert body["state"] == "IN_PROGRESS"
    assert body["current"]["axisCode"] == "L1"
    assert body["current"]["hintsUsed"] == 2
    assert body["current"]["hintText"] == "L1 힌트 2"


def test_grading_failure_returns_a_retryable_error(monkeypatch, score):
    """채점이 깨지면 **파싱 가능한 503**이 나가야 한다.

    안 잡으면 처리되지 않은 500이 나가는데 본문이 비어 있어 프론트가 파싱조차
    못 한다 — 학생 화면에 아무 안내도 못 띄운다(2026-08-02 실호출에서 발견).
    무료 티어 실패율이 32%라 드문 경로가 아니다.
    """
    from app.engines.analysis.stages import StageError

    sid = _start()

    def _boom(*a, **k):
        raise StageError("p04-5: PROVIDER_ERROR", [{"status": "FAILED"}])

    monkeypatch.setattr(sessions_mod.grading, "grade", _boom)

    r = client.post(f"/api/v0/sessions/{sid}/answers",
                    json={"clientRequestId": "boom", "answerText": "답변"}, headers=HEADERS)

    assert r.status_code == 503
    assert r.json()["error"] == "GRADING_UNAVAILABLE"
    assert r.json()["retryable"] is True          # 같은 키로 재전송하면 된다


def test_failed_turn_is_not_recorded(monkeypatch, score):
    """실패한 턴이 기록되면 재전송이 중복 턴을 만든다. 커서도 안 움직여야 한다."""
    from app.engines.analysis.stages import StageError

    sid = _start()
    monkeypatch.setattr(sessions_mod.grading, "grade",
                        lambda *a, **k: (_ for _ in ()).throw(StageError("터짐", [])))
    client.post(f"/api/v0/sessions/{sid}/answers",
                json={"clientRequestId": "same", "answerText": "답변"}, headers=HEADERS)

    view = client.get(f"/api/v0/sessions/{sid}", headers=HEADERS).json()
    assert view["transcript"] == []
    assert view["current"]["axisCode"] == "L1"    # 커서 그대로
