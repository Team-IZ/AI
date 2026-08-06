""" 문답 세션 진행 + 채점. **무상태다**(2026-08-03, PLAN §T11).

AI는 세션을 들고 있지 않는다 — 문제·기록·커서가 매 요청에 실려 오고, 응답의 커서를
다음 요청에 그대로 실어 보내면 진행된다. 그래서 여기 테스트는 그 왕복을 그대로 흉내내며
**계단 규칙**을 잰다: 언제 다음 단계로 가고, 언제 힌트가 열리고, 언제 문제가 끝나는가.

채점기는 가짜다. 재는 것은 모델 품질이 아니라 판정에 따른 **진행 규칙**이다.
"""
import uuid
from datetime import datetime, timezone

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
        "title": f"결제 처리 {no}", "snippetKey": f"p{no}-" + "a" * 16,
        "codeLanguage": "PYTHON",
        "priority": 1.0, "sourcePath": "app/pay.py", "lineStart": 4, "lineEnd": 7,
        "codeSnippet": "def pay(order, method):", "contentHash": "a" * 64, "evidenceHash": "a" * 64,
        "extractorVersion": 1, "teachId": f"teach-{no}",
        "stages": [_stage(a) for a in ("L1", "L2", "L3", "L4")],
    }


PROBLEMS = [_problem(1), _problem(2)]


@pytest.fixture
def score(monkeypatch):
    """채점 점수를 시나리오로 준다. 다 쓰면 마지막 값을 반복한다."""
    class _Plan(list):
        """점수 시나리오 + 채점기에 실려 간 인자(`seen`)."""
        seen: dict = {}

    plan = _Plan([4])

    def _grade(axis_code, question, answer, *, model_code, hints=None, **kw):
        value = plan[0] if len(plan) == 1 else plan.pop(0)
        used = len(hints or [])
        # 채점기에 무엇이 실려 갔는지. 모델·맥락 배선을 재는 테스트가 읽는다.
        plan.seen = {"model_code": model_code, **kw}
        return grading.Grade(
            axis_code=axis_code, score=value, hints_used=used,
            passed=value >= grading.scoring.PASS_SCORE,
            autonomy=grading.scoring.autonomy_for(used),
            matched_level="", evidence="", missing="", usages=[],
        )

    monkeypatch.setattr(sessions_mod.grading, "grade", _grade)
    return plan


class Backend:
    """백엔드 흉내. 커서와 기록을 들고 다니며 매 요청에 실어 보낸다.

    **AI가 아무것도 기억하지 않는다**는 사실이 이 클래스로 드러난다.
    """

    def __init__(self, problems=PROBLEMS, session_id=None):
        self.problems = problems
        # 멱등 캐시가 프로세스 전역이라 세션 id가 겹치면 앞 테스트의 응답이 돌아온다.
        self.session_id = session_id or f"sess-{uuid.uuid4()}"
        self.cursor = None          # 첫 답변은 커서 없이 — AI가 첫 문제 L1로 본다
        self.transcript: list[dict] = []
        self.n = 0

    def answer(self, text="답변입니다", request_id=None, **override) -> dict:
        self.n += 1
        body = {
            "clientRequestId": request_id or f"r{self.n}",
            "answerText": text,
            "problems": self.problems,
            "transcript": self.transcript,
            "cursor": self.cursor,
            **override,
        }
        r = client.post(f"/api/v0/sessions/{self.session_id}/answers",
                        json=body, headers=HEADERS)
        out = r.json()
        if r.status_code == 200:
            self.cursor = out["cursor"]
            if out["turn"]:
                self.transcript.append(out["turn"])
        return out


def test_stateless_endpoints_are_gone():
    """세션 시작·조회·복원은 삭제됐다(§T11 B). 남은 것은 답변 제출 하나뿐이다."""
    assert client.post("/api/v0/sessions", json={}, headers=HEADERS).status_code == 404
    assert client.get("/api/v0/sessions/s-1", headers=HEADERS).status_code == 404
    assert client.post("/api/v0/sessions/s-1/restore", json={},
                       headers=HEADERS).status_code == 404


def test_first_answer_without_cursor_starts_at_l1(score):
    """커서를 안 주면 첫 문제의 L1이다. 질문은 동결분을 꺼내 쓸 뿐 만들지 않는다."""
    body = Backend().answer()

    assert body["turn"]["axisCode"] == "L1"
    assert body["turn"]["questionText"] == "L1 질문"
    assert body["current"]["codeContext"]["path"] == "app/pay.py"
    assert body["progress"]["problemTotal"] == 2


def test_no_problems_means_nothing_to_grade(score):
    """문제를 안 주면 물을 것이 없다. 지어내지 않는다 — 그게 근거 전제를 깬다."""
    body = Backend(problems=[]).answer()

    assert body["state"] == "COMPLETED"
    assert body["turn"] is None
    assert body["current"] is None
    assert body["cursor"] is None


def test_pass_climbs_to_the_next_axis(score):
    """통과하면 다음 단계로. 계단은 건너뛰지 않는다."""
    body = Backend().answer()

    assert body["cursor"] == {"problemId": "prob-1", "axisCode": "L2", "hintsUsed": 0, "mac": None}
    assert body["current"]["axisCode"] == "L2"


def test_fail_opens_a_hint_and_keeps_the_question(score):
    """미달이면 힌트가 열리고 **같은 질문을 다시 묻는다**.

    힌트는 재진술이라 원 질문을 대체하지 않는다 — questionText가 그대로여야 한다.
    """
    score[:] = [2]

    body = Backend().answer()

    assert body["cursor"] == {"problemId": "prob-1", "axisCode": "L1", "hintsUsed": 1, "mac": None}
    assert body["current"]["questionText"] == "L1 질문"   # 같은 질문
    assert body["current"]["hintText"] == "L1 힌트 1"


def test_second_failure_opens_the_second_hint(score):
    score[:] = [2]
    session = Backend()
    session.answer()

    body = session.answer()

    assert body["current"]["hintText"] == "L1 힌트 2"
    assert body["current"]["hintsUsed"] == 2


def test_exhausted_hints_end_the_problem(score):
    """힌트를 다 쓰고도 미달이면 그 문제는 끝이다. 다음 단계를 던지지 않는다."""
    score[:] = [2]
    session = Backend()
    session.answer()
    session.answer()

    body = session.answer()

    assert body["cursor"]["problemId"] == "prob-2"       # 다음 문제로
    assert body["cursor"]["axisCode"] == "L1"            # 그 문제의 L1부터
    assert len(session.transcript) == 3                  # 세 시도가 다 기록된다


def test_hint_usage_is_recorded_in_the_turn(score):
    """점수는 그대로 나가고(상한 폐기), 어느 슬롯인지는 hintsUsed 가 정한다."""
    score[:] = [2, 5]
    session = Backend()
    session.answer()                                     # 미달 → 힌트 1

    turn = session.answer()["turn"]

    assert turn["score"] == 5                            # 눌러 담지 않는다
    assert turn["passed"] is True
    assert turn["hintsUsed"] == 1                        # = problem_stage 의 첫 힌트 슬롯
    assert turn["hintText"] == "L1 힌트 1"               # 어떤 힌트를 보고 답했는지


def test_completing_all_axes_moves_to_the_next_problem(score):
    """L4까지 통과하면 그 문제는 완주. 다음 문제의 L1로 간다."""
    session = Backend()
    for _ in range(4):
        body = session.answer()

    assert body["cursor"] == {"problemId": "prob-2", "axisCode": "L1", "hintsUsed": 0, "mac": None}


def test_session_completes_after_the_last_problem(score):
    """문제 2개 × 4단계 = 8턴이면 끝."""
    session = Backend()
    for _ in range(8):
        body = session.answer()

    assert body["state"] == "COMPLETED"
    assert body["current"] is None
    assert body["cursor"] is None
    assert len(session.transcript) == 8


def test_termination_reason_is_reported_when_hints_run_out(score):
    """왜 끝났는지를 AI가 말해야 한다.

    종료 판정은 AI가 소유하는데 응답이 커서만 주면 백엔드는 "커서가 다음 문제로
    넘어갔으니 끝났나 보다"로 역추론해야 한다. DB assessment_problem에
    termination_reason·ended_level 자리가 이미 있다.
    """
    score[:] = [2]
    session = Backend()
    session.answer()                                     # 힌트 1
    mid = session.answer()                               # 힌트 2

    assert mid["terminationReason"] is None              # 아직 진행 중
    assert mid["endedLevel"] is None

    body = session.answer()                              # 소진 후에도 미달 → 종료

    assert body["terminationReason"] == "TERMINATED_AT_L1"
    assert body["endedLevel"] == "L1"


def test_completing_all_axes_reports_completion(score):
    """L4까지 통과하면 종료가 아니라 완주다 — 사유 코드가 다르다."""
    session = Backend()
    for _ in range(3):
        body = session.answer()
        assert body["terminationReason"] is None         # L1~L3 통과 중에는 안 붙는다

    body = session.answer()                              # L4 통과 → 완주

    assert body["terminationReason"] == "COMPLETED_L4"
    assert body["endedLevel"] == "L4"


def test_provider_model_code_reaches_the_grader(score):
    """채점 모델은 operator가 고른다(GradingPolicy) — 요청 값이 서버 기본값을 이긴다."""
    Backend().answer(providerModelCode="vendor/some-model-1")

    assert score.seen["model_code"] == "vendor/some-model-1"


def test_grading_model_falls_back_to_the_server_default(score):
    """생략하면 서버 기본값. `/analyses`·`/curricula`·`/reports`와 같은 규칙이다."""
    Backend().answer()

    assert score.seen["model_code"] == get_settings().model_code_session


def test_analysis_context_is_passed_to_the_grader(score):
    """코드 파편만으로는 전체 흐름이 안 보인다. 두 필드만 넘어가야 한다."""
    context = {"overview": "결제 서비스", "structure": []}

    Backend().answer(analysisContext=context)

    assert score.seen["analysis_context"] == context


def test_same_client_request_id_is_idempotent(score):
    """같은 clientRequestId 재전송 → 동일 응답. 커서가 두 번 밀리지 않는다."""
    session = Backend()
    first = session.answer(request_id="dup")
    second = session.answer(request_id="dup")

    assert first == second
    assert first["cursor"]["axisCode"] == "L2"


def test_transcript_replays_when_the_cursor_is_missing(score):
    """커서가 없으면 transcript를 되짚는다 — **매 요청이 곧 restore다.**

    턴 수만 세면 안 된다. 힌트 후 재질의도 한 턴이라 같은 단계에서 세 턴이 나올 수 있고,
    그러면 커서가 세 칸 밀린다. 아래는 L1에서 두 번 미달한 기록이다.
    """
    score[:] = [2]
    turn = {"problemId": "prob-1", "axisCode": "L1", "questionText": "L1 질문",
            "answerText": "a", "answeredAt": "2026-08-02T00:00:00Z",
            "score": 2, "passed": False, "hintsUsed": 0}
    session = Backend()
    session.transcript = [turn, {**turn, "hintsUsed": 1}]

    body = session.answer(cursor=None)

    assert body["turn"]["hintText"] == "L1 힌트 2"       # 힌트 2개가 열린 자리였다
    assert body["turn"]["hintsUsed"] == 2


def test_cursor_wins_over_the_transcript(score):
    """커서가 오면 그대로 믿는다 — 백엔드 장부가 원본이다."""
    body = Backend().answer(cursor={"problemId": "prob-2", "axisCode": "L3", "hintsUsed": 1})

    assert body["turn"]["problemId"] == "prob-2"
    assert body["turn"]["axisCode"] == "L3"
    assert body["turn"]["hintText"] == "L3 힌트 1"


def test_ai_usage_is_reported_for_grading(monkeypatch):
    """🔴 채점 토큰이 원장에 실려야 한다 (PLAN §T11 F1).

    **비용은 Spring이 계산한다**가 계약인데 토큰을 안 보내면 근거가 없다. 채점은
    학생 수 × 문제 3 × 축 4라 호출 건수가 가장 많은 경로다 — 여기가 비면 원장이 통째로 빈다.
    """
    def _grade(axis_code, question, answer, *, model_code, hints=None, **kw):
        return grading.Grade(
            axis_code=axis_code, score=4, hints_used=0,
            passed=True, autonomy="SELF", matched_level="", evidence="", missing="",
            usages=[{"model_code": "m-1", "input_token_count": 100,
                     "output_token_count": 20, "cached_token_count": 0,
                     "status": "SUCCEEDED", "failure_code": None, "latency_ms": 1200,
                     "occurred_at": datetime.now(timezone.utc)}],
        )

    monkeypatch.setattr(sessions_mod.grading, "grade", _grade)

    usage = Backend(session_id="usage-1").answer()["aiUsage"]

    assert len(usage) == 1
    assert usage[0]["featureCode"] == "ANSWER_EVALUATION"
    assert usage[0]["contextType"] == "ASSESSMENT_SESSION"
    assert usage[0]["contextId"] == "usage-1"
    assert usage[0]["inputTokenCount"] == 100
    assert "estimatedCost" not in usage[0]      # 금액은 Spring이 계산한다


def test_grading_failure_returns_a_retryable_error(monkeypatch, score):
    """채점이 깨지면 **파싱 가능한 503**이 나가야 한다.

    안 잡으면 처리되지 않은 500이 나가는데 본문이 비어 있어 프론트가 파싱조차
    못 한다 — 학생 화면에 아무 안내도 못 띄운다(2026-08-02 실호출에서 발견).
    무료 티어 실패율이 32%라 드문 경로가 아니다.
    """
    from app.engines.analysis.stages import StageError

    def _boom(*a, **k):
        raise StageError("p04-5: PROVIDER_ERROR", [{"status": "FAILED"}])

    monkeypatch.setattr(sessions_mod.grading, "grade", _boom)

    body = Backend().answer()

    assert body["error"] == "GRADING_UNAVAILABLE"
    assert body["retryable"] is True          # 같은 키로 재전송하면 된다


def test_failed_turn_does_not_poison_the_retry(monkeypatch, score):
    """실패한 턴은 기록되지 않는다. 같은 키로 재전송하면 정상 채점돼야 한다."""
    from app.engines.analysis.stages import StageError

    session = Backend()
    fake_grade = sessions_mod.grading.grade   # score 픽스처가 꽂아둔 가짜
    state = {"fail": True}

    def _flaky(*a, **k):
        if state["fail"]:
            state["fail"] = False
            raise StageError("터짐", [])
        return fake_grade(*a, **k)

    monkeypatch.setattr(sessions_mod.grading, "grade", _flaky)
    session.answer(request_id="same")          # 503. 아무것도 기록되지 않는다

    body = session.answer(request_id="same")   # 같은 키로 재전송 → 정상 채점

    assert body["turn"]["axisCode"] == "L1"   # 커서가 안 밀렸다
    assert body["cursor"]["axisCode"] == "L2"


def test_answer_endpoint_stays_sync():
    """채점 경로는 `def`여야 한다 — `async def`로 되돌리면 동시 처리가 1명이 된다.

    LLM 호출(`nvidia_client`)이 `urllib.request` 기반 블로킹이라 `async def`
    안에서 부르면 이벤트 루프가 채점 내내 멈춘다. FastAPI는 `def` 엔드포인트만
    스레드풀로 옮겨준다. LLM 호출을 비동기로 바꾸기 전까지 이 시그니처를 지킨다.
    """
    import inspect

    from app.api.sessions import submit_answer

    assert not inspect.iscoroutinefunction(submit_answer)


def test_grading_gets_the_fragment_not_the_whole_file():
    """codeSnippet은 파일 전체다. 그대로 넣으면 4,000자 상한에 앞에서부터 잘려
    문제 구간이 파일 뒤쪽일 때 근거가 사라진 채 채점된다."""
    from app import sessions as sessions_mod

    text = "\n".join(f"line {i}" for i in range(1, 501))
    code = sessions_mod._grading_code(
        {"code_snippet": text, "line_start": 400, "line_end": 402}
    )

    assert "line 400" in code and "line 402" in code
    assert "line 1" not in code.splitlines()      # 앞부분은 안 들어간다
    assert len(code) < len(text) / 5


def test_grading_code_survives_a_fragment_only_snippet():
    """파일이 너무 커서 파편만 온 경우 줄 번호가 그 문자열의 색인이 아니다."""
    from app import sessions as sessions_mod

    assert sessions_mod._grading_code(
        {"code_snippet": "only one line", "line_start": 900, "line_end": 900}
    ) == "only one line"
