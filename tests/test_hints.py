""" p04-7 힌트 생성.

두 가지를 잰다:
  ① 힌트가 **재진술**인가 — 범위를 좁히면 측정 대상이 바뀐다(PM 설계 v2 §4-2)
  ② 병렬 배치에서 **순서와 레벨이 안 섞이는가** — 섞이면 힌트2가 힌트1 자리에 들어가고
     점수 상한(5/4/3)이 엉뚱한 시도에 걸린다. 에러는 안 난다.
"""
import time

from app.engines.analysis import hints, scoring, stages


def _level(values) -> int:
    """`hint_level`은 `"1 (다른 표현으로)"` 꼴로 프롬프트에 들어간다 — 앞 숫자만 쓴다."""
    return int(str(values["hint_level"]).split()[0])


def _fake(monkeypatch, text_for=lambda level, question: f"[{level}] {question}"):
    """호출 인자를 그대로 되비추는 가짜. 어느 질문의 몇 번 힌트인지 추적한다."""
    def _call(stage_id, values, *, model_code, fallback_model_code=None, max_attempts=2, timeout_s=None):
        return stages.StageResult(
            data={"hint": text_for(_level(values), values["question"])},
            usages=[{"status": "SUCCEEDED"}],
        )

    monkeypatch.setattr(hints.stages, "call", _call)


def test_ladder_spec_reaches_the_prompt(monkeypatch):
    """사다리 강도가 프롬프트에 들어가야 재진술 규칙이 모델에 전달된다."""
    seen = {}

    def _call(stage_id, values, *, model_code, fallback_model_code=None, max_attempts=2, timeout_s=None):
        seen[_level(values)] = values["hint_strength_spec"]
        return stages.StageResult(data={"hint": "재진술"}, usages=[])

    monkeypatch.setattr(hints.stages, "call", _call)

    hints.freeze_for_stage("원 질문", model_code="m")

    assert "축소가 아니다" in seen[2]
    assert seen[1] == scoring.HINT_LADDER[1]["spec"]


def test_fallback_does_not_narrow_or_point_at_code():
    """폴백이라고 범위를 좁히면, 하필 생성이 실패한 학생만 다른 것을 측정당한다.

    위치를 짚어주는 것도 금지다 — 답의 일부를 주는 것이다.
    """
    for level in (1, 2):
        text = hints.fallback(level)
        assert "좁혀" not in text
        assert ".py" not in text and ":" not in text


def test_guard_violation_falls_back(monkeypatch):
    """힌트에 보기가 섞이면 사다리 최강 단계를 공짜로 주는 셈이다."""
    _fake(monkeypatch, lambda level, q: "다음 중 무엇인가요? ① A ② B")

    hint = hints.generate(1, "원 질문", model_code="m")

    assert hint.generated is False          # 폴백이 쓰였다는 것을 감사할 수 있어야 한다
    assert "①" not in hint.text


def test_freeze_many_keeps_order_and_levels(monkeypatch):
    """병렬이라 완료 순서가 뒤섞인다. 결과는 **요청 순서**로 돌아와야 한다."""
    _fake(monkeypatch)

    specs = [{"question": f"질문{i}"} for i in range(5)]
    result = hints.freeze_many(specs, model_code="m")

    assert len(result) == 5
    for i, pair in enumerate(result):
        assert [h.hint_level for h in pair] == [1, 2]
        assert pair[0].text == f"[1] 질문{i}"
        assert pair[1].text == f"[2] 질문{i}"


def test_freeze_many_actually_runs_in_parallel(monkeypatch):
    """순차면 8콜 × 0.1초 = 0.8초. 병렬이면 그보다 훨씬 짧아야 한다."""
    def _call(stage_id, values, *, model_code, fallback_model_code=None, max_attempts=2, timeout_s=None):
        time.sleep(0.1)
        return stages.StageResult(data={"hint": "재진술"}, usages=[])

    monkeypatch.setattr(hints.stages, "call", _call)

    started = time.monotonic()
    hints.freeze_many([{"question": f"q{i}"} for i in range(4)], model_code="m")
    took = time.monotonic() - started

    assert took < 0.5, f"병렬이 안 돌고 있다: {took:.2f}s"


def test_one_broken_hint_does_not_stop_the_batch(monkeypatch):
    """한 힌트가 깨졌다고 배치 전체를 잃으면 나머지 콜의 토큰이 헛돈다."""
    def _call(stage_id, values, *, model_code, fallback_model_code=None, max_attempts=2, timeout_s=None):
        if values["question"] == "q1":
            raise stages.StageError("p04-7: 터짐", [{"status": "FAILED"}])
        return stages.StageResult(data={"hint": "재진술"}, usages=[{"status": "SUCCEEDED"}])

    monkeypatch.setattr(hints.stages, "call", _call)

    result = hints.freeze_many([{"question": "q0"}, {"question": "q1"}], model_code="m")

    assert [h.generated for h in result[0]] == [True, True]
    assert [h.generated for h in result[1]] == [False, False]   # 폴백으로 채워진다
    assert all(h.text for pair in result for h in pair)          # 빈 힌트는 없다


def test_empty_batch_is_a_no_op(monkeypatch):
    _fake(monkeypatch)

    assert hints.freeze_many([], model_code="m") == []
