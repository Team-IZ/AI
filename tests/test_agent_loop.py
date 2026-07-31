""" app/engines/shared/agent_loop.py -- 도구 호출 루프 테스트. 네트워크는 전혀 안 나간다 """
from app.engines.shared.agent_loop import run_tool_loop
from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import ChatResult, LlmTimeoutError

BUDGET = CallBudget(
    feature_code="CODE_ANALYSIS", source_type="CODE_MAP",
    max_llm_calls=8, max_tool_rounds=4, max_attempts_per_call=3, timeout_s=600,
)


def _result(content="", tool_calls=(), finish_reason="stop"):
    return ChatResult(
        content=content, finish_reason=finish_reason,
        input_tokens=10, output_tokens=5, cached_tokens=0, tool_calls=tool_calls,
    )


def _sequence_chat_fn(results):
    calls = {"n": 0}

    def chat_fn(**kwargs):
        i = calls["n"]
        calls["n"] += 1
        return results[i]
    chat_fn.calls = calls
    return chat_fn


def test_no_tool_calls_returns_after_single_round():
    chat_fn = _sequence_chat_fn([_result("final answer")])
    messages = [{"role": "user", "content": "hi"}]

    result, ai_usage = run_tool_loop(
        model_code="m", messages=messages, tools=[], tool_registry={},
        max_tokens=100, budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )

    assert result.content == "final answer"
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "SUCCEEDED"
    assert chat_fn.calls["n"] == 1


def test_tool_call_executes_registry_fn_and_feeds_result_back():
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"key": "x"}'}}
    chat_fn = _sequence_chat_fn([
        _result("", tool_calls=(tool_call,)),
        _result("used the tool result"),
    ])
    calls_seen = []

    def lookup(args):
        calls_seen.append(args)
        return "42"

    messages = [{"role": "user", "content": "look up x"}]
    result, ai_usage = run_tool_loop(
        model_code="m", messages=messages, tools=[{"type": "function", "function": {"name": "lookup"}}],
        tool_registry={"lookup": lookup}, max_tokens=100, budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )

    assert result.content == "used the tool result"
    assert calls_seen == [{"key": "x"}]
    assert len(ai_usage) == 2
    # 도구 호출/결과가 메시지 히스토리에 남는다
    assert messages[-2]["role"] == "assistant"
    assert messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "42"}


def test_unknown_tool_name_feeds_back_not_found_without_crashing():
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "ghost", "arguments": "{}"}}
    chat_fn = _sequence_chat_fn([
        _result("", tool_calls=(tool_call,)),
        _result("done"),
    ])
    messages = [{"role": "user", "content": "x"}]

    run_tool_loop(
        model_code="m", messages=messages, tools=[], tool_registry={},
        max_tokens=100, budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )

    assert messages[-1]["content"] == "TOOL_NOT_FOUND: ghost"


def test_tool_exception_feeds_back_error_string_without_crashing():
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "boom", "arguments": "{}"}}
    chat_fn = _sequence_chat_fn([
        _result("", tool_calls=(tool_call,)),
        _result("done"),
    ])

    def boom(args):
        raise ValueError("kaboom")

    messages = [{"role": "user", "content": "x"}]
    run_tool_loop(
        model_code="m", messages=messages, tools=[], tool_registry={"boom": boom},
        max_tokens=100, budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )

    assert messages[-1]["content"] == "TOOL_ERROR: ValueError: kaboom"


def test_malformed_tool_arguments_json_falls_back_to_empty_dict():
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "not json"}}
    chat_fn = _sequence_chat_fn([
        _result("", tool_calls=(tool_call,)),
        _result("done"),
    ])
    captured = []

    messages = [{"role": "user", "content": "x"}]
    run_tool_loop(
        model_code="m", messages=messages, tools=[], tool_registry={"lookup": lambda a: captured.append(a) or "ok"},
        max_tokens=100, budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )

    assert captured == [{}]


def test_max_tool_rounds_stops_calling_even_if_model_keeps_requesting_tools():
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
    always_wants_tool = _result("", tool_calls=(tool_call,))
    chat_fn = _sequence_chat_fn([always_wants_tool] * 10)  # 무한히 tool_calls를 요청한다고 가정

    small_budget = CallBudget(
        feature_code="CODE_ANALYSIS", source_type="CODE_MAP",
        max_llm_calls=8, max_tool_rounds=2, max_attempts_per_call=3, timeout_s=600,
    )
    messages = [{"role": "user", "content": "x"}]
    result, ai_usage = run_tool_loop(
        model_code="m", messages=messages, tools=[], tool_registry={"lookup": lambda a: "ok"},
        max_tokens=100, budget=small_budget, job_id="job-1", chat_fn=chat_fn,
    )

    assert chat_fn.calls["n"] == 2  # max_tool_rounds=2를 넘겨 부르지 않는다
    assert len(ai_usage) == 2
    assert result.tool_calls  # 마지막 응답은 여전히 tool_calls를 담은 채로 반환된다(호출자가 판단)


def test_chat_fn_exception_returns_failed_usage_and_empty_result():
    def chat_fn(**kwargs):
        raise LlmTimeoutError("too slow")

    messages = [{"role": "user", "content": "x"}]
    result, ai_usage = run_tool_loop(
        model_code="m", messages=messages, tools=[], tool_registry={},
        max_tokens=100, budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )

    assert result.content == ""
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "FAILED"
    assert ai_usage[0].failure_code == "TIMEOUT"


def test_uses_budget_feature_code_and_source_type_in_ai_usage():
    chat_fn = _sequence_chat_fn([_result("ok")])
    messages = [{"role": "user", "content": "x"}]
    _, ai_usage = run_tool_loop(
        model_code="m", messages=messages, tools=[], tool_registry={},
        max_tokens=100, budget=BUDGET, job_id="job-42", chat_fn=chat_fn,
    )
    assert ai_usage[0].source_id == "job-42"
    assert ai_usage[0].idempotency_key == "job-42:CODE_MAP:1"
