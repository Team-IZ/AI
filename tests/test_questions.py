""" p04-4 질문 동결(T7b). 생성물을 믿지 않는 지점이다.

**동결 대상은 L1~L4 전부다** (2026-08-02 전면 동결). 세션 중 질문 생성은 없다.
"""
from app.engines.analysis import questions, stages

TOPIC = {
    "teach_id": "t1", "title": "토큰 발급", "rationale": "신뢰 경계",
    "code_ref": {"file": "app/auth.py", "line_start": 1, "line_end": 2,
                 "snippet": "def issue_token(user):\n    return sign(user)"},
}


def _levels(**overrides):
    """매니페스트 p04-4는 아직 L1~L4를 한 번에 만든다. 그 상태를 그대로 흉내낸다."""
    base = {
        "L1_코드기술": "이 함수가 무엇을 하나요?",
        "L2_설계논리": "왜 이렇게 설계했나요?",
        "L3_대안": "다른 방법과 비교하면 어떤가요?",
        "L4_반례한계": "언제 깨지나요?",
    }
    base.update(overrides)
    return [{"axis": k, "question": v} for k, v in base.items()]


def _fake(monkeypatch, *responses):
    """호출마다 다음 응답을 돌려준다. 재생성 경로를 보기 위함."""
    calls = []

    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        calls.append(values)
        data = responses[min(len(calls) - 1, len(responses) - 1)]
        return stages.StageResult(data=data, usages=[{"status": "SUCCEEDED"}])

    monkeypatch.setattr(questions.stages, "call", _call)
    return calls


def test_all_four_axes_are_frozen(monkeypatch):
    """4축 전부 동결한다. 하나라도 빠지면 그 단계에 질문 없는 화면이 뜬다."""
    _fake(monkeypatch, {"levels": _levels()})

    qs = questions.freeze(TOPIC, {}, None, model_code="m")

    assert qs.flagged is False
    assert [lv["axis_code"] for lv in qs.levels] == ["L1", "L2", "L3", "L4"]


def test_shuffled_axes_are_reordered(monkeypatch):
    """모델이 순서를 흔들어도 결과는 항상 진행 순서다 — 순서가 곧 진행 순서다."""
    _fake(monkeypatch, {"levels": list(reversed(_levels()))})

    qs = questions.freeze(TOPIC, {}, None, model_code="m")

    assert [lv["axis_code"] for lv in qs.levels] == ["L1", "L2", "L3", "L4"]


def test_missing_frozen_axis_is_rejected(monkeypatch):
    """L2가 빠지면 학생이 L2에서 질문 없는 화면을 보고 그게 0점으로 기록된다."""
    without_l2 = [lv for lv in _levels() if lv["axis"] != "L2_설계논리"]
    _fake(monkeypatch, {"levels": without_l2})

    qs = questions.freeze(TOPIC, {}, None, model_code="m")

    assert qs.flagged is True
    assert "형태 불일치" in qs.reason


def test_choices_trigger_regeneration_then_flag(monkeypatch):
    """선택지가 계속 섞이면 flagged로 남긴다 — 조용히 통과시키지 않는다."""
    bad = {"levels": _levels(L2_설계논리="동기 방식과 비동기 방식 중 무엇이 나은가요?")}
    calls = _fake(monkeypatch, bad)

    qs = questions.freeze(TOPIC, {}, None, model_code="m")

    assert qs.flagged is True
    assert "선택지 위반" in qs.reason
    assert len(calls) == 3          # 최초 1회 + 재생성 2회 (max_regenerations)


def test_violation_in_late_axis_is_caught(monkeypatch):
    """L3에 선택지가 섞이면 재생성하고, 끝내 안 고쳐지면 flagged로 남긴다.

    전면 동결 전에는 L3·L4를 안 써서 무시했다. 이제는 저장돼 학생에게 그대로
    나가므로 앞 축과 똑같이 막아야 한다.
    """
    _fake(monkeypatch, {"levels": _levels(L3_대안="다음 중 무엇인가요?")})

    qs = questions.freeze(TOPIC, {}, None, model_code="m")

    assert qs.flagged is True
    assert "L3" in (qs.reason or "")


def test_regeneration_recovers(monkeypatch):
    """1차가 위반이어도 2차가 통과하면 그걸 쓴다."""
    bad = {"levels": _levels(L1_코드기술="다음 중 무엇인가요?")}
    good = {"levels": _levels()}
    calls = _fake(monkeypatch, bad, good)

    qs = questions.freeze(TOPIC, {}, None, model_code="m")

    assert qs.flagged is False
    assert len(calls) == 2
