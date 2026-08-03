""" ai_usage 스키마. DB CHECK를 여기서 먼저 거는지 확인. """
import pytest
from pydantic import ValidationError

from app.schemas.usage import AiUsage

BASE = {
    "featureCode": "GRADING",
    "modelCode": "nvidia/llama-3.3-70b-instruct",
    "contextType": "GRADING",
    "contextId": "ans-1",
    "requestId": "req-1",
    "traceId": "trace-1",
    "idempotencyKey": "ans-1:GRADING:1",
    "inputTokenCount": 1200,
    "outputTokenCount": 300,
    "cachedTokenCount": 0,
    "status": "SUCCEEDED",
    "latencyMs": 1400,
    "occurredAt": "2026-07-30T12:00:00Z",
}


def test_succeeded_must_not_carry_failure_code():
    """DB CHECK: SUCCEEDED면 failure_code가 NULL이어야 한다."""
    with pytest.raises(ValidationError):
        AiUsage.model_validate({**BASE, "failureCode": "TIMEOUT"})


def test_failed_must_carry_failure_code():
    """DB CHECK: FAILED·PARTIAL이면 failure_code가 있어야 한다."""
    with pytest.raises(ValidationError):
        AiUsage.model_validate({**BASE, "status": "FAILED"})

    ok = AiUsage.model_validate({**BASE, "status": "FAILED", "failureCode": "RATE_LIMITED"})
    assert ok.failure_code == "RATE_LIMITED"


def test_cached_cannot_exceed_input():
    """DB CHECK: cached_token_count <= input_token_count."""
    with pytest.raises(ValidationError):
        AiUsage.model_validate({**BASE, "cachedTokenCount": 9999})


def test_context_type_is_a_closed_set():
    """2026-08-03 확정(§T11 D-1). 자유 문자열이면 Spring이 원장을 못 묶는다."""
    assert set(AiUsage.model_json_schema()["properties"]["contextType"]["enum"]) == {
        "ANALYSIS", "GRADING", "REPORT", "CURRICULUM"
    }
    with pytest.raises(ValidationError):
        AiUsage.model_validate({**BASE, "contextType": "SESSION_ANSWER"})


def test_no_cost_fields():
    """비용은 Spring이 계산한다(C-3). AI 응답에 금액이 있으면 담당 경계가 깨진다."""
    assert not {"inputUnitPrice", "outputUnitPrice", "estimatedCost", "currencyCode"} & set(
        AiUsage.model_json_schema()["properties"]
    )