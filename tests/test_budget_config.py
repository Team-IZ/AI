""" app/engines/shared/budget.py -- 예산 설정 로딩 테스트

D6: max_llm_calls를 크루 코드에 매직넘버로 박지 않고 파일 하나로 분리했다는 것을
"파일을 바꾸면 실제로 로딩값이 바뀐다"로 증명한다(judgment/importance_rank.py의
_load_weights() 검증 패턴과 동일).
"""
import json

import pytest

from app.engines.shared.budget import CallBudget, load_budget


def test_defaults_load_from_committed_json():
    budget = load_budget("CODE_MAP")
    assert budget == CallBudget(
        feature_code="CODE_ANALYSIS", source_type="CODE_MAP",
        max_llm_calls=8, max_tool_rounds=4, max_attempts_per_call=3, timeout_s=600,
    )


def test_falls_back_to_module_constants_when_file_missing(tmp_path):
    budget = load_budget("CODE_MAP", path=tmp_path / "does_not_exist.json")
    assert budget.max_llm_calls == 8  # 모듈 상수 폴백


def test_falls_back_to_module_constants_when_json_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    budget = load_budget("CODE_MAP", path=bad)
    assert budget.max_llm_calls == 8


def test_unknown_key_raises_when_no_default_exists(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"budgets": {}}), encoding="utf-8")
    with pytest.raises(KeyError):
        load_budget("NOT_A_REAL_KEY", path=empty)


def test_overriding_file_actually_changes_loaded_value(tmp_path):
    """ 파일을 바꾸면 로딩값이 바뀐다 -- 데이터/로직 분리 증명 """
    override = tmp_path / "override.json"
    override.write_text(json.dumps({
        "budgets": {"CODE_MAP": {
            "feature_code": "CODE_ANALYSIS", "source_type": "CODE_MAP",
            "max_llm_calls": 1, "max_tool_rounds": 1, "max_attempts_per_call": 1, "timeout_s": 30,
        }},
    }), encoding="utf-8")
    budget = load_budget("CODE_MAP", path=override)
    assert budget.max_llm_calls == 1
    assert budget.timeout_s == 30


def test_max_llm_calls_and_max_tool_rounds_are_independently_configurable(tmp_path):
    """ D8이 아직 안 끝났으니 두 숫자가 하나로 합쳐지지 않았는지 -- 서로 다른 값을
    넣었을 때 둘 다 그 값 그대로 보존되는지로 확인한다(합쳐졌다면 하나가 다른 값을
    덮어썼을 것). """
    override = tmp_path / "override.json"
    override.write_text(json.dumps({
        "budgets": {"CODE_MAP": {
            "feature_code": "CODE_ANALYSIS", "source_type": "CODE_MAP",
            "max_llm_calls": 8, "max_tool_rounds": 3, "max_attempts_per_call": 2, "timeout_s": 120,
        }},
    }), encoding="utf-8")
    budget = load_budget("CODE_MAP", path=override)
    assert budget.max_llm_calls == 8
    assert budget.max_tool_rounds == 3
