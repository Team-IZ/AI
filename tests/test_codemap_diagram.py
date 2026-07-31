""" app/engines/codemap/diagram.py (p05-4) -- 구조도 생성 테스트. 네트워크는 전혀 안 나간다 """
from app.engines.codemap.diagram import (
    check_mermaid_syntax,
    mermaid_syntax_lookup,
    run_diagram_stage,
)
from app.engines.codemap.models import AnalysisDoc, StructureArea
from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import ChatResult

BUDGET = CallBudget(
    feature_code="CODE_ANALYSIS", source_type="DIAGRAM",
    max_llm_calls=1, max_tool_rounds=3, max_attempts_per_call=3, timeout_s=600,
)

DOC_WITH_STRUCTURE = AnalysisDoc(
    overview="", structure=(StructureArea(area="core", files=("main.py",), role="핵심"),),
    decision_points=(), risks=(),
)
DOC_EMPTY = AnalysisDoc(overview="", structure=(), decision_points=(), risks=())


def _result(content, tool_calls=()):
    return ChatResult(content=content, finish_reason="stop", input_tokens=10, output_tokens=5, cached_tokens=0, tool_calls=tool_calls)


def _sequence_chat_fn(results):
    calls = {"n": 0}

    def chat_fn(**kwargs):
        i = calls["n"]
        calls["n"] += 1
        return results[i]
    chat_fn.calls = calls
    return chat_fn


# --- mermaid_syntax_lookup ---

def test_lookup_returns_cheatsheet_for_known_type():
    result = mermaid_syntax_lookup({"diagram_type": "flowchart"})
    assert "flowchart TD" in result


def test_lookup_unknown_type_names_supported_list_without_crashing():
    result = mermaid_syntax_lookup({"diagram_type": "ganttChart"})
    assert result.startswith("UNKNOWN_DIAGRAM_TYPE")
    assert "flowchart" in result and "classDiagram" in result


# --- check_mermaid_syntax ---

def test_valid_flowchart_passes():
    valid, reason = check_mermaid_syntax("flowchart TD\n  A[x] --> B[y]")
    assert valid and reason is None


def test_strips_fences_before_validating():
    valid, reason = check_mermaid_syntax("```mermaid\nflowchart TD\n  A[x] --> B[y]\n```")
    assert valid and reason is None


def test_empty_content_fails():
    valid, reason = check_mermaid_syntax("   ")
    assert not valid and reason == "EMPTY"


def test_unknown_diagram_header_fails():
    valid, reason = check_mermaid_syntax("this is not mermaid at all")
    assert not valid and reason == "UNKNOWN_DIAGRAM_TYPE_HEADER"


def test_unbalanced_brackets_fail():
    valid, reason = check_mermaid_syntax("flowchart TD\n  A[x --> B[y]")
    assert not valid and reason == "UNBALANCED_[]"


# --- run_diagram_stage ---

def test_empty_structure_returns_empty_without_calling_chat():
    calls = {"n": 0}

    def chat_fn(**kwargs):
        calls["n"] += 1
        return _result("flowchart TD\n  A --> B")

    mermaid, ai_usage = run_diagram_stage(
        doc=DOC_EMPTY, model_code="m", budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert (mermaid, ai_usage) == ("", [])
    assert calls["n"] == 0


def test_zero_budget_returns_empty_without_calling_chat():
    zero_budget = CallBudget(
        feature_code="CODE_ANALYSIS", source_type="DIAGRAM",
        max_llm_calls=0, max_tool_rounds=3, max_attempts_per_call=3, timeout_s=600,
    )
    calls = {"n": 0}

    def chat_fn(**kwargs):
        calls["n"] += 1
        return _result("flowchart TD\n  A --> B")

    mermaid, ai_usage = run_diagram_stage(
        doc=DOC_WITH_STRUCTURE, model_code="m", budget=zero_budget, job_id="job-1", chat_fn=chat_fn,
    )
    assert (mermaid, ai_usage) == ("", [])
    assert calls["n"] == 0


def test_successful_call_returns_validated_mermaid_and_usage():
    chat_fn = _sequence_chat_fn([_result("flowchart TD\n  core[core] --> util[util]")])
    mermaid, ai_usage = run_diagram_stage(
        doc=DOC_WITH_STRUCTURE, model_code="m", budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert mermaid == "flowchart TD\n  core[core] --> util[util]"
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "SUCCEEDED"
    assert ai_usage[0].source_type == "DIAGRAM"


def test_model_calls_lookup_tool_before_final_answer():
    """ 모델이 문법이 불확실해 도구를 먼저 부르고, 그 다음 라운드에서 최종 답을 낸다 """
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "mermaid_syntax_lookup", "arguments": '{"diagram_type": "flowchart"}'}}
    chat_fn = _sequence_chat_fn([
        _result("", tool_calls=(tool_call,)),
        _result("flowchart TD\n  core[core] --> util[util]"),
    ])
    mermaid, ai_usage = run_diagram_stage(
        doc=DOC_WITH_STRUCTURE, model_code="m", budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert mermaid == "flowchart TD\n  core[core] --> util[util]"
    assert len(ai_usage) == 2  # 도구 호출 라운드 + 최종 라운드


def test_invalid_final_content_falls_back_to_empty_without_failing_the_call():
    """ 호출 자체는 성공했지만(SUCCEEDED) 산출물이 구조적으로 무효면 다이어그램만 뺀다(D2/D6) """
    chat_fn = _sequence_chat_fn([_result("이건 mermaid가 아니라 그냥 설명문이다")])
    mermaid, ai_usage = run_diagram_stage(
        doc=DOC_WITH_STRUCTURE, model_code="m", budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert mermaid == ""
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "SUCCEEDED"  # 호출은 성공, 검증만 실패


def test_chat_failure_falls_back_to_empty_with_failed_usage():
    def chat_fn(**kwargs):
        raise RuntimeError("network exploded")

    mermaid, ai_usage = run_diagram_stage(
        doc=DOC_WITH_STRUCTURE, model_code="m", budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert mermaid == ""
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "FAILED"


def test_truncates_structure_block_per_stage_truncation_config():
    huge_doc = AnalysisDoc(
        overview="", decision_points=(), risks=(),
        structure=tuple(StructureArea(area=f"area{i}", files=(f"f{i}.py",), role="x" * 200) for i in range(50)),
    )
    captured = {}

    def chat_fn(*, messages, **kwargs):
        captured["user"] = messages[1]["content"]
        return _result("flowchart TD\n  A --> B")

    run_diagram_stage(doc=huge_doc, model_code="m", budget=BUDGET, job_id="job-1", chat_fn=chat_fn)
    assert len(captured["user"]) < sum(len(s.role) for s in huge_doc.structure)
