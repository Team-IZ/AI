""" stages.call()의 fallback_model_code 동작.

2026-08-25 nemotron RATE_LIMITED 인시던트 대응 — 1차 모델이 재시도까지 소진하면
설정된 폴백 모델로 한 번 더 돈다. 여기서 확인하는 것은 두 갈래뿐이다:
"소진 시 실제로 넘어가는가"와 "넘어가면 안 되는 실패는 안 넘어가는가".
"""
import pytest

from app.engines.analysis import stages


def test_falls_back_after_primary_model_exhausts_a_retryable_failure(monkeypatch):
    seen_models = []

    def _chat(model_code, messages, **kwargs):
        seen_models.append(model_code)
        if model_code == "primary":
            raise stages.client.LlmError(
                "429", {"status": "FAILED", "failure_code": "RATE_LIMITED"})
        return stages.client.LlmResult(
            content='{"ok": true}', usage={"status": "SUCCEEDED"}, raw={})

    monkeypatch.setattr(stages.client, "chat", _chat)

    result = stages.call("p01-2", {"chunk_range": "1-2", "chunk_text": "본문"},
                         model_code="primary", fallback_model_code="fallback",
                         max_attempts=1)

    assert seen_models == ["primary", "fallback"]
    assert result.data == {"ok": True}
    # 1차 실패분도 usage 원장에 남는다 -- 실제로 나간 비용이다.
    assert [u.get("failure_code") for u in result.usages if "failure_code" in u] == ["RATE_LIMITED"]


def test_does_not_fall_back_when_failure_is_not_in_the_trigger_set(monkeypatch):
    """INVALID_JSON류는 모델을 바꿔도 같은 자리에서 또 깨질 수 있어 폴백 대상이 아니다."""
    seen_models = []

    def _chat(model_code, messages, **kwargs):
        seen_models.append(model_code)
        raise stages.client.LlmError(
            "잘못된 요청", {"status": "FAILED", "failure_code": "INVALID_REQUEST"})

    monkeypatch.setattr(stages.client, "chat", _chat)

    with pytest.raises(stages.StageError):
        stages.call("p01-2", {"chunk_range": "1-2", "chunk_text": "본문"},
                    model_code="primary", fallback_model_code="fallback",
                    max_attempts=1)

    assert seen_models == ["primary"]   # fallback은 한 번도 안 불렸다


def test_no_fallback_model_code_behaves_exactly_like_before(monkeypatch):
    """fallback_model_code를 안 주면 기존 call() 호출부는 전혀 영향받지 않는다."""
    def _chat(model_code, messages, **kwargs):
        raise stages.client.LlmError(
            "429", {"status": "FAILED", "failure_code": "RATE_LIMITED"})

    monkeypatch.setattr(stages.client, "chat", _chat)

    with pytest.raises(stages.StageError):
        stages.call("p01-2", {"chunk_range": "1-2", "chunk_text": "본문"},
                    model_code="primary", max_attempts=1)
