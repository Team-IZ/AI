""" app/engines/shared/llm.py -- 유일한 HTTP 지점 테스트. 네트워크는 전혀 안 나간다
(transport를 페이크로 주입) -- httpx는 요청/응답 모양만 흉내내는 값객체로 대체한다.
"""
import httpx
import pytest

from app.engines.shared.llm import (
    ChatResult,
    LlmContextOverflowError,
    LlmInvalidJsonError,
    LlmProviderError,
    LlmRateLimitedError,
    LlmTimeoutError,
    chat,
    extract_json_object,
    reasoning_effort_for,
)

MESSAGES = [{"role": "user", "content": "hi"}]


def _resp(status_code=200, json_body=None, text=""):
    request = httpx.Request("POST", "https://example.test")
    return httpx.Response(status_code, json=json_body, text=text if json_body is None else None, request=request)


def _ok_transport(content_field="content"):
    def transport(url, *, json, headers, timeout):
        return _resp(200, {
            "choices": [{"message": {content_field: "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 2}},
        })
    return transport


def test_successful_call_returns_chat_result():
    result = chat(
        model_code="z-ai/glm-5.2", messages=MESSAGES, max_tokens=100,
        api_key="test-key", transport=_ok_transport(),
    )
    assert result == ChatResult(
        content="hello", finish_reason="stop", input_tokens=10, output_tokens=5, cached_tokens=2,
    )


def test_falls_back_to_reasoning_content_when_content_empty():
    """ D131/D142: 일부 모델은 JSON 모드 답을 content가 아니라 reasoning_content에 남긴다 """
    result = chat(
        model_code="step-3.5-flash", messages=MESSAGES, max_tokens=100,
        api_key="k", transport=_ok_transport(content_field="reasoning_content"),
    )
    assert result.content == "hello"


def test_returns_finish_reason_even_on_success():
    def transport(url, *, json, headers, timeout):
        return _resp(200, {"choices": [{"message": {"content": "x"}, "finish_reason": "length"}], "usage": {}})

    result = chat(model_code="m", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport)
    assert result.finish_reason == "length"


def test_extract_json_strips_code_fences():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_falls_back_to_brace_slice():
    assert extract_json_object('here is the answer: {"a": 1} -- done') == {"a": 1}


def test_extract_json_raises_invalid_json_when_unparseable():
    with pytest.raises(LlmInvalidJsonError):
        extract_json_object("no json here at all")


def test_maps_429_to_rate_limited(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def transport(url, *, json, headers, timeout):
        return _resp(429, text="rate limited")

    with pytest.raises(LlmRateLimitedError):
        chat(model_code="m", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport, max_attempts=1)


def test_maps_400_context_to_context_overflow():
    def transport(url, *, json, headers, timeout):
        return _resp(400, text="maximum context length exceeded")

    with pytest.raises(LlmContextOverflowError):
        chat(model_code="m", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport, max_attempts=3)


def test_maps_500_to_provider_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def transport(url, *, json, headers, timeout):
        return _resp(500, text="internal error")

    with pytest.raises(LlmProviderError):
        chat(model_code="m", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport, max_attempts=1)


def test_httpx_timeout_maps_to_llm_timeout_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def transport(url, *, json, headers, timeout):
        raise httpx.TimeoutException("timed out")

    with pytest.raises(LlmTimeoutError):
        chat(model_code="m", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport, max_attempts=1)


def test_retries_up_to_max_attempts_with_backoff(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def transport(url, *, json, headers, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return _resp(500, text="fail")
        return _resp(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})

    result = chat(model_code="m", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport, max_attempts=3)
    assert result.content == "ok"
    assert calls["n"] == 3
    assert sleeps == [2, 4]  # 지수 백오프: 2**1, 2**2 (마지막 성공 시도 전에는 안 잔다)


def test_invalid_json_failure_does_not_retry(monkeypatch):
    """ 재시도해도 같은 결과일 실패(빈 응답)는 max_attempts와 무관하게 즉시 올라간다 """
    calls = {"n": 0}

    def transport(url, *, json, headers, timeout):
        calls["n"] += 1
        return _resp(200, {"choices": [{"message": {}, "finish_reason": "stop"}], "usage": {}})

    with pytest.raises(LlmInvalidJsonError):
        chat(model_code="m", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport, max_attempts=5)
    assert calls["n"] == 1


def test_never_sends_reasoning_effort_to_unlisted_model():
    assert reasoning_effort_for("mistralai/mistral-medium-3.5-128b") is None
    assert reasoning_effort_for("stepfun-ai/step-3.7-flash") == "low"

    captured = {}

    def transport(url, *, json, headers, timeout):
        captured["body"] = json
        return _resp(200, {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}], "usage": {}})

    chat(model_code="mistralai/mistral-medium-3.5-128b", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport)
    assert "reasoning_effort" not in captured["body"]

    chat(model_code="stepfun-ai/step-3.7-flash", messages=MESSAGES, max_tokens=10, api_key="k", transport=transport)
    assert captured["body"]["reasoning_effort"] == "low"


def test_records_ai_usage_entry_via_timer():
    """ chat()의 반환값이 LlmCallTimer.build()에 그대로 흘러 AiUsageEntry가 되는지 --
    Phase A에서 만든 계측 메커니즘과 실제로 맞물리는지 확인 """
    from app.engines.shared.timing import LlmCallTimer

    timer = LlmCallTimer("CODE_ANALYSIS", "z-ai/glm-5.2", source_type="CODE_MAP", source_id="job-1", attempt_no=1)
    with timer:
        result = chat(model_code="z-ai/glm-5.2", messages=MESSAGES, max_tokens=10, api_key="k", transport=_ok_transport())
    entry = timer.build(
        input_token_count=result.input_tokens,
        output_token_count=result.output_tokens,
        cached_token_count=result.cached_tokens,
        status="SUCCEEDED",
    )
    assert entry.input_token_count == 10
    assert entry.output_token_count == 5
    assert entry.cached_token_count == 2
    assert entry.status == "SUCCEEDED"


def test_api_key_defaults_to_nvidia_api_key_secret(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("NVIDIA_API_KEY", "env-key-123")
    get_settings.cache_clear()

    captured = {}

    def transport(url, *, json, headers, timeout):
        captured["headers"] = headers
        return _resp(200, {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}], "usage": {}})

    try:
        chat(model_code="m", messages=MESSAGES, max_tokens=10, transport=transport)
        assert captured["headers"]["Authorization"] == "Bearer env-key-123"
    finally:
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        get_settings.cache_clear()
