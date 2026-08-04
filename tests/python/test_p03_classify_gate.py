"""shared/p03-engine.js::classifyAnswer() 판정 게이트 회귀 테스트 (2026-08-04, redteam 감사 C1).

실행: python3 -m pytest tests/python/ -q   (저장소 루트에서)

버그: classify_justification()/evaluate_reflection()은 substantive(20자 미만 필터)를
정확히 계산해 반환했지만, classifyAnswer()가 Pyodide로 돌리는 Python 블록이 그 값을
안 읽고 원시 matched_categories/optional_matches 개수만으로 verdict를 매겼다 -- 20자
미만 답변도 패턴만 맞으면 defended가 나갔다.

검증 방법: classify_justification/evaluate_reflection을 통제된 값을 반환하는 가짜
모듈로 치환한 뒤, shared/p03-engine.js에 실제로 배포되는 Python 소스를 그대로 추출해
exec한다 -- 게이트 로직을 테스트에 다시 베끼지 않고 실물을 검증한다
(tests/js/p03-code-context.test.js와 같은 원칙: 구현을 복제하지 말고 require/추출해서 쓴다).
"""
import json
import os
import re
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_PATH = os.path.join(REPO_ROOT, "shared", "p03-engine.js")


def _extract_classify_python():
    """classifyAnswer()가 pyodide.runPython()에 넘기는 실제 Python 소스를 그대로 뽑는다."""
    with open(ENGINE_PATH, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"pyodide\.runPython\(`\nimport json\n(.*?)\n`\);", src, re.DOTALL)
    assert m, "classifyAnswer()의 runPython 블록을 못 찾음 -- p03-engine.js 구조가 바뀌었는지 확인"
    return "import json\n" + m.group(1)


CLASSIFY_SOURCE = _extract_classify_python()


def _run_classify(category, level, *, isolation_result=None, reflection_result=None):
    """CLASSIFY_SOURCE를 통제된 가짜 분류기 모듈로 exec하고 verdict만 돌려준다."""
    fake_isolation = types.ModuleType("isolation_classifier")
    fake_isolation.classify_justification = lambda answer: isolation_result
    fake_reflection = types.ModuleType("reflection_signal")
    fake_reflection.evaluate_reflection = lambda answer: reflection_result

    saved = {
        "isolation_classifier": sys.modules.get("isolation_classifier"),
        "reflection_signal": sys.modules.get("reflection_signal"),
    }
    sys.modules["isolation_classifier"] = fake_isolation
    sys.modules["reflection_signal"] = fake_reflection
    try:
        ns = {"_category": category, "_answer": "placeholder", "_level": level}
        exec(CLASSIFY_SOURCE, ns)  # 배포되는 실물 소스를 그대로 실행하는 지점
        return json.loads(ns["_classify_result"])["verdict"]
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# ---- cognition-isolation ----

def test_isolation_not_substantive_forces_surface_even_with_matches():
    """20자 미만(substantive=False)이면 패턴이 2개 매치돼도 surface여야 한다(수정 전엔 defended)."""
    verdict = _run_classify(
        "cognition-isolation", "l1",
        isolation_result={
            "justified": False, "substantive": False,
            "matched_categories": ["a", "b"], "categories": {},
        },
    )
    assert verdict == "surface"


def test_isolation_substantive_with_two_matches_is_defended():
    """substantive=True일 때의 기존 동작(매치 2개=defended)은 그대로 보존돼야 한다."""
    verdict = _run_classify(
        "cognition-isolation", "l1",
        isolation_result={
            "justified": True, "substantive": True,
            "matched_categories": ["a", "b"], "categories": {},
        },
    )
    assert verdict == "defended"


def test_isolation_substantive_with_one_match_is_partial():
    verdict = _run_classify(
        "cognition-isolation", "l1",
        isolation_result={
            "justified": True, "substantive": True,
            "matched_categories": ["a"], "categories": {},
        },
    )
    assert verdict == "partial"


# ---- reflection 레벨 ----

def test_reflection_not_substantive_forces_surface_even_if_required_and_optional_ok():
    """substantive=False면 required_ok+optional 충분이어도 surface여야 한다(수정 전엔 defended)."""
    verdict = _run_classify(
        "cognition-generic", "reflection",
        reflection_result={
            "reflection_present": False, "substantive": False,
            "required_ok": True, "optional_matches": 3, "min_optional_required": 2,
        },
    )
    assert verdict == "surface"


def test_reflection_substantive_and_sufficient_is_defended():
    verdict = _run_classify(
        "cognition-generic", "reflection",
        reflection_result={
            "reflection_present": True, "substantive": True,
            "required_ok": True, "optional_matches": 3, "min_optional_required": 2,
        },
    )
    assert verdict == "defended"


# ---- 일반 레벨(그 외) ----

def test_generic_level_not_substantive_forces_surface():
    """일반 레벨 분기도 substantive=False면 optional_matches와 무관하게 surface여야 한다(수정 전엔 defended)."""
    verdict = _run_classify(
        "cognition-generic", "l2",
        reflection_result={
            "reflection_present": False, "substantive": False,
            "required_ok": False, "optional_matches": 2, "min_optional_required": 2,
        },
    )
    assert verdict == "surface"


def test_generic_level_substantive_with_two_optional_is_defended():
    verdict = _run_classify(
        "cognition-generic", "l2",
        reflection_result={
            "reflection_present": True, "substantive": True,
            "required_ok": False, "optional_matches": 2, "min_optional_required": 2,
        },
    )
    assert verdict == "defended"
