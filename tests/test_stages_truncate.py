""" _truncate()의 JSON 보존 (2026-08-11).

`analysis_block`은 JSON 문자열이다. 문자로 잘라 보내면 중괄호가 안 닫혀 모델이 통째로
못 읽는데, **에러가 안 난다** — "모델이 분석 문서를 무시했다"로만 보인다. 실측 문서는
3.3k라 예산 6,000에 안 걸리지만, `decisionPoints` 개수에 상한이 없어 넘길 수는 있다.
"""
import json

from app.engines.analysis.stages import _truncate


def _document(points: int) -> dict:
    return {
        "overview": "주문 처리 파이프라인이다.",
        "structure": [{"area": f"영역{i}", "files": [f"a{i}.py"], "role": "역할"}
                      for i in range(points)],
        "decisionPoints": [{"title": f"결정{i}", "sourcePath": f"a{i}.py",
                            "symbol": "def f():", "whyItMatters": "이유 " * 20}
                           for i in range(points)],
        "risks": [f"위험{i}" for i in range(points)],
    }


def test_oversized_json_block_stays_parseable():
    """핵심 회귀 — 잘려도 JSON이어야 한다."""
    text = json.dumps(_document(40), ensure_ascii=False)
    assert len(text) > 6000

    out = _truncate({"analysis_block": text}, {"analysis_block": 6000})["analysis_block"]

    assert len(out) <= 6000
    parsed = json.loads(out)               # 여기가 안 터지는 게 전부다
    assert parsed["overview"] == "주문 처리 파이프라인이다."   # 요약은 살아남는다
    assert len(parsed["decisionPoints"]) < 40                  # 꼬리는 깎였다


def test_block_within_budget_is_untouched():
    """실측 크기(3.3k)에서는 아무 일도 일어나지 않아야 한다 — 항목을 괜히 잃으면 안 된다."""
    text = json.dumps(_document(3), ensure_ascii=False)
    assert len(text) < 6000

    out = _truncate({"analysis_block": text}, {"analysis_block": 6000})

    assert out["analysis_block"] == text


def test_plain_text_block_still_gets_a_character_cut():
    """code_block은 JSON이 아니다. 예전 동작 그대로 문자로 자른다."""
    out = _truncate({"code_block": "x" * 100}, {"code_block": 10})

    assert out["code_block"] == "x" * 10
