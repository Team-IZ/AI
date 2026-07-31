""" p04-5 채점(T7c). 힌트 상한이 여기서 걸린다. """
import pytest

from app.engines.analysis import grading, stages


def _fake(monkeypatch, data):
    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        _call.values = values
        return stages.StageResult(data=data, usages=[{"status": "SUCCEEDED"}])

    monkeypatch.setattr(grading.stages, "call", _call)
    return _call


def _data(score=4):
    return {"score": score, "matched_level": "전체 흐름은 정확하나…",
            "evidence": "학생이 인용한 부분", "missing": "데이터 흐름 연결"}


def test_hint_cap_lowers_confirmed_score(monkeypatch):
    """원점수는 그대로 두고 상한만 씌운다 — '몇 번째 힌트에서 통과했나'가 자력의 측정값이다."""
    _fake(monkeypatch, _data(score=5))

    g = grading.grade("L1", "q", "a", model_code="m", hints=["h1", "h2"])

    assert g.best_score == 5           # 루브릭 원점수는 보존
    assert g.confirmed_score == 3      # 힌트 2회 상한
    assert g.autonomy == "PARTIAL"


def test_pass_uses_confirmed_not_best(monkeypatch):
    """best로 판정하면 힌트 상한이 무력해진다."""
    _fake(monkeypatch, _data(score=5))

    g = grading.grade("L1", "q", "a", model_code="m", hints=["h1", "h2"])

    assert g.passed is True            # 상한 3점 = 통과선 3점
    assert grading.grade("L1", "q", "a", model_code="m", hints=["h1", "h2"]).confirmed_score == 3


def test_no_hint_keeps_full_score(monkeypatch):
    _fake(monkeypatch, _data(score=5))

    g = grading.grade("L1", "q", "a", model_code="m")

    assert (g.best_score, g.confirmed_score, g.autonomy) == (5, 5, "SELF")


def test_below_pass_line_fails(monkeypatch):
    _fake(monkeypatch, _data(score=2))

    g = grading.grade("L2", "q", "a", model_code="m")

    assert g.passed is False


def test_hints_are_sent_to_the_grader(monkeypatch):
    """모델이 힌트를 봐야 '힌트를 보고도 못 맞혔다'를 루브릭대로 판정할 수 있다."""
    call = _fake(monkeypatch, _data())

    grading.grade("L1", "q", "a", model_code="m", hints=["관점을 다시 보세요"])

    assert call.values["hints_used"] == 1
    assert "관점을 다시 보세요" in call.values["hints_block"]


def test_non_integer_score_is_an_error(monkeypatch):
    """0점으로 밀면 학생이 억울하게 깎인다. 실패로 올려 재시도에 맡긴다."""
    _fake(monkeypatch, {"score": "네 점", "matched_level": "", "evidence": "", "missing": ""})

    with pytest.raises(stages.StageError):
        grading.grade("L1", "q", "a", model_code="m")