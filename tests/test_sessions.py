""" 7단계 세션 엔드포인트 스텁 테스트. """
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

START_BODY = {"attemptId": "a-1", "timeLimitSec": 1200}


def _start() -> str:
    """세션 하나 만들고 sessionId 반환."""
    r = client.post("/api/v0/sessions", json=START_BODY, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["sessionId"]


def test_start_returns_first_question():
    """세션 시작 → 201 + IN_PROGRESS + 첫 질문(sequenceNo 1)."""
    r = client.post("/api/v0/sessions", json=START_BODY, headers=HEADERS)

    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "IN_PROGRESS"
    assert body["current"]["sequenceNo"] == 1
    assert body["progress"]["problemTotal"] == 2


def test_answer_advances_to_next_question():
    """답변 1개 제출 → 다음 질문(sequenceNo 2)."""
    sid = _start()

    r = client.post(
        f"/api/v0/sessions/{sid}/answers",
        json={"clientRequestId": "req-1", "answerText": "제 의도는..."},
        headers=HEADERS,
    )

    assert r.json()["current"]["sequenceNo"] == 2


def test_answering_all_questions_completes_session():
    """질문 다 답하면 COMPLETED + transcript 2턴."""
    sid = _start()
    client.post(f"/api/v0/sessions/{sid}/answers",
                json={"clientRequestId": "r1", "answerText": "a1"}, headers=HEADERS)
    r = client.post(f"/api/v0/sessions/{sid}/answers",
                    json={"clientRequestId": "r2", "answerText": "a2"}, headers=HEADERS)

    body = r.json()
    assert body["state"] == "COMPLETED"
    assert len(body["transcript"]) == 2
    assert body["current"] is None


def test_same_client_request_id_is_idempotent():
    """같은 clientRequestId 재전송 → 동일 응답, 중복 턴 없음."""
    sid = _start()
    first = client.post(f"/api/v0/sessions/{sid}/answers",
                        json={"clientRequestId": "dup", "answerText": "a"}, headers=HEADERS)
    second = client.post(f"/api/v0/sessions/{sid}/answers",
                         json={"clientRequestId": "dup", "answerText": "a"}, headers=HEADERS)

    assert first.json() == second.json()  # 응답 동일
    # 커서가 두 번 안 밀렸는지: 여전히 2번 질문
    assert first.json()["current"]["sequenceNo"] == 2


def test_get_session_returns_state():
    """GET으로 현재 상태 조회."""
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


def test_restore_rebuilds_from_transcript():
    """transcript 1턴으로 복원 → 이어질 질문(sequenceNo 2)부터."""
    r = client.post(
        "/api/v0/sessions/restored-1/restore",
        json={
            "timeLimitSec": 1200,
            "transcript": [
                {"problemId": "prob-stub-1", "axisCode": "L1",
                 "questionText": "q1", "answerText": "a1", "answeredAt": "2026-07-23T00:00:00Z",
                 "bestScore": 4, "confirmedScore": 4, "attemptCount": 1, "autonomy": "SELF"}
            ],
        },
        headers=HEADERS,
    )

    body = r.json()
    assert body["state"] == "IN_PROGRESS"
    assert body["current"]["sequenceNo"] == 2
    
def test_turn_carries_score():
    """턴마다 채점 결과가 실려야 Spring이 problem_stage에 쓸 값이 있다."""
    sid = _start()
    body = client.post(f"/api/v0/sessions/{sid}/answers",
                       json={"clientRequestId": "s1", "answerText": "a"},
                       headers=HEADERS).json()

    turn = body["transcript"][0]
    assert turn["confirmedScore"] is not None
    assert turn["attemptCount"] >= 1
    assert turn["autonomy"] in {"SELF", "SELF_MAINTAINED", "PARTIAL"}