""" p04-5 채점(T7c). 힌트 상한이 여기서 걸린다. """
import pytest

from app.engines.analysis import grading, stages


def _fake(monkeypatch, data):
    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None,
              extra_user=""):
        _call.values = values
        _call.extra_user = extra_user
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

def test_reach_criterion_reaches_the_grader(monkeypatch):
    """축별 도달 기준이 프롬프트에 실려야 모델이 도달을 따로 판정할 수 있다.

    매니페스트는 vendor라 본문을 못 고친다 — rubric_block으로 넣는다(scoring.REACH_CRITERIA).
    """
    call = _fake(monkeypatch, _data())

    grading.grade("L3", "q", "a", model_code="m")

    assert "도달 경계는 3점" in call.values["rubric_block"]
    assert "대안 하나를 구체적으로 말했는가" in call.values["rubric_block"]


def test_model_reach_is_recorded(monkeypatch):
    """vendor P-1 — 모델이 낸 도달 판정을 그대로 들고 있는다."""
    _fake(monkeypatch, {**_data(score=4), "reached": True})

    g = grading.grade("L1", "q", "a", model_code="m")

    assert g.model_reached is True
    assert g.passed is True
    assert g.reach_conflict is False


def test_score_wins_when_reach_disagrees(monkeypatch):
    """모델 판정과 점수가 어긋나면 점수를 따르고, 어긋났다는 사실을 남긴다.

    점수를 따르는 이유: 힌트 상한이 점수에 걸리므로 통과가 점수와 따로 놀면
    "5점인데 미달" 같은 상태가 생긴다. 불일치를 버리지 않는 이유: 루브릭 문구와
    도달 기준이 다른 말을 하고 있다는 신호다.
    """
    _fake(monkeypatch, {**_data(score=5), "reached": False})

    g = grading.grade("L1", "q", "a", model_code="m")

    assert g.passed is True            # 점수를 따른다
    assert g.model_reached is False
    assert g.reach_conflict is True    # 어긋난 사실은 남는다


def test_missing_reach_field_does_not_break_grading(monkeypatch):
    """P-1이 사라져도(갱신으로 덮이거나 상류가 다르게 가도) 채점은 계속 돌아야 한다."""
    _fake(monkeypatch, _data(score=4))   # reached 없음

    g = grading.grade("L1", "q", "a", model_code="m")

    assert g.model_reached is None
    assert g.reach_conflict is False
    assert g.passed is True


def test_analysis_context_reaches_the_grader(monkeypatch):
    """코드 파편 밖의 구조가 프롬프트에 실려야 한다.

    MVC면 model·view·controller가 다른 파일에 있다. 파편만 본 채점기는 학생이
    "컨트롤러가 서비스에 위임한다"고 답해도 사실 여부를 모른다.
    """
    call = _fake(monkeypatch, _data())

    grading.grade("L1", "q", "a", model_code="m", analysis_context={
        "overview": "주문을 받아 결제로 넘기는 서비스다.",
        "structure": [{"area": "컨트롤러", "files": ["app/api.py"], "role": "요청 수신"}],
    })

    assert "주문을 받아 결제로 넘기는 서비스다." in call.extra_user
    assert "app/api.py" in call.extra_user
    # 맥락이 루브릭을 밀어내면 안 된다 — 채점 기준은 값 단계 서술뿐이다.
    assert "기준을 늘리지 마라" in call.extra_user


def test_analysis_context_is_optional(monkeypatch):
    """안 주면 지금과 똑같이 동작해야 한다(하위 호환)."""
    call = _fake(monkeypatch, _data())

    grading.grade("L1", "q", "a", model_code="m")

    assert call.extra_user == ""


def test_decision_points_are_not_sent(monkeypatch):
    """분석 문서를 통째로 넣으면 채점 36회 × 5,000~7,000토큰이다.

    `decisionPoints`는 문제 후보 전체 목록이고 문제는 이미 정해졌다 — 부피만 차지한다.
    """
    call = _fake(monkeypatch, _data())

    grading.grade("L1", "q", "a", model_code="m", analysis_context={
        "overview": "개요",
        "structure": [],
        "decision_points": [{"title": "여기 있으면 안 된다"}],
        "risks": ["이것도"],
    })

    assert "여기 있으면 안 된다" not in call.extra_user
    assert "이것도" not in call.extra_user
