""" LLM 래퍼(T7a). 네트워크 없이 응답 해석·실패 분류만 검증한다. """
import urllib.error

import pytest

from app.llm import client


def _body(content, reasoning="생각 중...", finish="stop", usage=None):
    return {
        "choices": [{"message": {"content": content, "reasoning_content": reasoning},
                     "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


class _FakeClient:
    def __init__(self, body):
        self._body = body

    def __call__(self, pool=None, timeout_s=None):
        return self

    def chat(self, model, messages, **kwargs):
        return self._body


@pytest.fixture
def fake_vendor(monkeypatch):
    """vendor 대신 가짜를 꽂는다. 키도 네트워크도 안 쓴다."""
    def _install(body):
        fake = _FakeClient(body)
        monkeypatch.setattr(client, "_pool", object())
        monkeypatch.setattr(client, "_load_vendor", lambda: (fake, None, _Exhausted))
    return _install


class _Exhausted(Exception):
    pass


def test_content_wins_over_reasoning(fake_vendor):
    """답은 content다. reasoning_content로 대체하면 사고 과정이 답으로 새어나간다."""
    fake_vendor(_body("실제 답"))

    r = client.chat("m", [{"role": "user", "content": "q"}])

    assert r.content == "실제 답"
    assert r.usage["status"] == "SUCCEEDED"


def test_truncated_output_is_an_error(fake_vendor):
    """출력이 잘려 content가 비면 실패다 — 빈 문자열을 조용히 돌려주면
    다음 단계가 "모델이 JSON을 안 줬다"로 오진한다."""
    fake_vendor(_body("", finish="length"))

    with pytest.raises(client.LlmError) as exc:
        client.chat("m", [{"role": "user", "content": "q"}])

    assert exc.value.usage["failure_code"] == "CONTEXT_OVERFLOW"
    assert exc.value.usage["status"] == "FAILED"


def test_tokens_are_extracted(fake_vendor):
    """비용의 근거값이다. cached는 없을 수도 있으니 0으로 떨어져야 한다."""
    fake_vendor(_body("답", usage={"prompt_tokens": 100, "completion_tokens": 20,
                                   "prompt_tokens_details": {"cached_tokens": 30}}))

    u = client.chat("m", [{"role": "user", "content": "q"}]).usage

    assert (u["input_token_count"], u["output_token_count"], u["cached_token_count"]) == (100, 20, 30)


def test_429_is_rate_limited():
    """실패 코드가 틀리면 Spring이 재시도할지 포기할지 잘못 판단한다."""
    err = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)

    assert client._classify(err, str(err)) == "RATE_LIMITED"
    