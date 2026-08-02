""" p04-2 요구사항 P/F 판정.

핵심은 매칭이다. 모델이 하나를 빠뜨리면 인덱스로 붙일 때 그 뒤가 통째로 밀리고,
**학생이 통과한 요구사항에 F가 붙는다** — 에러가 안 나는 종류의 사고다.
"""
from app.engines.analysis import requirements, stages
from app.schemas.analysis import RequirementResult

FILES = {"app/pay.py": "def pay(order):\n    validate(order)\n    return charge(order)\n"}

REQS = [
    {"requirement_id": "r1", "text": "결제 전에 주문을 검증한다"},
    {"requirement_id": "r2", "text": "결제 실패를 로깅한다"},
    {"requirement_id": "r3", "text": "환불을 지원한다"},
]


def _fake(monkeypatch, data):
    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        _call.values = values
        return stages.StageResult(data=data, usages=[{"status": "SUCCEEDED"}])

    monkeypatch.setattr(requirements.stages, "call", _call)
    return _call


def _result(requirement: str, verdict: str, **extra):
    return {"requirement": requirement, "verdict": verdict, **extra}


def test_results_map_one_to_one(monkeypatch):
    """요청 requirements와 개수·순서가 정확히 맞아야 한다(jobs.py가 개수를 검사한다)."""
    _fake(monkeypatch, {"results": [
        _result("결제 전에 주문을 검증한다", "P"),
        _result("결제 실패를 로깅한다", "F"),
        _result("환불을 지원한다", "F"),
    ]})

    out = requirements.judge(REQS, FILES, model_code="m")

    assert [r["requirement_id"] for r in out.results] == ["r1", "r2", "r3"]
    assert [r["verdict"] for r in out.results] == ["P", "F", "F"]
    for r in out.results:
        RequirementResult.model_validate(r)


def test_shuffled_results_are_matched_by_text(monkeypatch):
    """모델이 순서를 흔들어도 텍스트로 붙인다 — 인덱스만 믿으면 판정이 뒤바뀐다."""
    _fake(monkeypatch, {"results": [
        _result("환불을 지원한다", "F"),
        _result("결제 전에 주문을 검증한다", "P"),
        _result("결제 실패를 로깅한다", "F"),
    ]})

    out = requirements.judge(REQS, FILES, model_code="m")

    assert {r["requirement_id"]: r["verdict"] for r in out.results} == {
        "r1": "P", "r2": "F", "r3": "F",
    }


def test_missing_result_does_not_shift_the_rest(monkeypatch):
    """모델이 r2를 빠뜨려도 r3 판정이 r2 자리로 밀리면 안 된다."""
    _fake(monkeypatch, {"results": [
        _result("결제 전에 주문을 검증한다", "P"),
        _result("환불을 지원한다", "P"),   # 위치로 붙이면 r2가 P가 돼버린다
    ]})

    out = requirements.judge(REQS, FILES, model_code="m")

    verdicts = {r["requirement_id"]: r["verdict"] for r in out.results}
    assert verdicts["r2"] == "F"          # 밀린 값을 쓰지 않았다
    assert verdicts["r3"] == "P"          # r3은 제 자리를 찾았다
    assert out.unmatched == ["r2"]
    assert "찾지 못했" in [r for r in out.results if r["requirement_id"] == "r2"][0]["note"]


def test_unlabeled_result_falls_back_to_position(monkeypatch):
    """텍스트를 안 달고 온 결과는 같은 자리의 요구사항으로 본다. 밀릴 위험이 없다."""
    _fake(monkeypatch, {"results": [
        _result("결제 전에 주문을 검증한다", "P"),
        {"verdict": "P"},                                  # 텍스트 없음
        _result("환불을 지원한다", "F"),
    ]})

    out = requirements.judge(REQS, FILES, model_code="m")

    assert {r["requirement_id"]: r["verdict"] for r in out.results}["r2"] == "P"
    assert out.unmatched == []


def test_non_p_verdicts_become_fail(monkeypatch):
    """'partial'·'PASS?' 같은 값은 전부 F다. 추정으로 통과시키지 않는다."""
    _fake(monkeypatch, {"results": [
        _result("결제 전에 주문을 검증한다", "partial"),
        _result("결제 실패를 로깅한다", "pass"),
        _result("환불을 지원한다", ""),
    ]})

    out = requirements.judge(REQS, FILES, model_code="m")

    assert [r["verdict"] for r in out.results] == ["F", "F", "F"]


def test_evidence_is_flattened_to_one_line(monkeypatch):
    """{file, lines, quote}를 위치와 인용이 둘 다 남는 한 줄로 만든다."""
    _fake(monkeypatch, {"results": [
        _result("결제 전에 주문을 검증한다", "P",
                evidence={"file": "app/pay.py", "lines": [2, 2], "quote": "validate(order)"}),
        _result("결제 실패를 로깅한다", "F", evidence={"file": None, "lines": [], "quote": ""}),
        _result("환불을 지원한다", "F"),
    ]})

    out = requirements.judge(REQS, FILES, model_code="m")

    assert out.results[0]["evidence"] == "app/pay.py:2 — validate(order)"
    assert out.results[1]["evidence"] is None
    assert out.results[2]["evidence"] is None


def test_empty_requirements_skips_the_call(monkeypatch):
    """판정할 게 없으면 부르지 않는다 — 빈 목록에 토큰을 태울 이유가 없다."""
    called = []
    monkeypatch.setattr(requirements.stages, "call",
                        lambda *a, **k: called.append(1))

    out = requirements.judge([], FILES, model_code="m")

    assert out.results == [] and out.usages == [] and called == []
