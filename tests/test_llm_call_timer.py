""" LlmCallTimer(app/engines/shared/timing.py) 단위 테스트

AiUsage 자체의 검증(DB CHECK 등)은 tests/test_usage.py가 이미 다룬다(develop의
app/schemas/usage.py) -- 여기서는 LlmCallTimer가 실제로 시간을 재고, AiUsage를
그 스키마 그대로 채워 만드는지만 확인한다.
"""
import time

import pytest

from app.engines.shared.timing import LlmCallTimer


def test_llm_call_timer_measures_real_elapsed_time():
    """ latency_ms가 하드코딩이 아니라 실제로 잰 값인지 -- with 블록 안에서 sleep을
    걸어보고 그 시간 이상이 찍히는지 확인한다(정확히 같을 필요는 없음, 최소 보장만). """
    timer = LlmCallTimer(
        "GRADING", "glm-5.2", source_type="GRADING_TURN", source_id="session-1", attempt_no=2,
    )
    with timer:
        time.sleep(0.05)
    assert timer.latency_ms >= 40  # 50ms 재웠으니 40ms 이상은 나와야 함(스케줄링 오차 감안)

    entry = timer.build(input_token_count=10, output_token_count=5, status="SUCCEEDED")
    assert entry.idempotency_key == "session-1:GRADING_TURN:2"
    assert entry.source_type == "GRADING_TURN"
    assert entry.source_id == "session-1"
    assert entry.latency_ms == timer.latency_ms


def test_llm_call_timer_records_latency_even_on_exception():
    """ 실패한 호출도 시간은 걸렸다 -- 예외가 나도 latency_ms는 확정돼야 한다 """
    timer = LlmCallTimer("GRADING", "glm-5.2", source_type="GRADING_TURN", source_id="s", attempt_no=1)
    with pytest.raises(RuntimeError):
        with timer:
            raise RuntimeError("boom")
    assert timer.latency_ms >= 0

    entry = timer.build(status="FAILED", failure_code="PROVIDER_ERROR")
    assert entry.status == "FAILED"
    assert entry.failure_code == "PROVIDER_ERROR"


def test_request_id_auto_generated_when_not_given():
    timer = LlmCallTimer("GRADING", "glm-5.2", source_type="GRADING_TURN", source_id="s", attempt_no=1)
    assert timer.request_id  # 비어있지 않은 uuid 문자열
    other = LlmCallTimer("GRADING", "glm-5.2", source_type="GRADING_TURN", source_id="s", attempt_no=1)
    assert timer.request_id != other.request_id  # 매 호출마다 새로 생성


def test_request_id_can_be_supplied_explicitly():
    timer = LlmCallTimer(
        "GRADING", "glm-5.2", source_type="GRADING_TURN", source_id="s", attempt_no=1,
        request_id="explicit-req-1",
    )
    assert timer.request_id == "explicit-req-1"


def test_trace_id_defaults_to_source_id_until_header_threading_exists():
    """ X-Trace-Id가 아직 API 레이어에서 안 꿰어져 있으므로(app/api/analyses.py 참고)
    자리표시자로 source_id를 쓴다 -- 실제 배선이 붙으면 이 기본값이 제거될 것 """
    timer = LlmCallTimer("GRADING", "glm-5.2", source_type="GRADING_TURN", source_id="job-99", attempt_no=1)
    assert timer.trace_id == "job-99"


def test_trace_id_can_be_supplied_explicitly():
    timer = LlmCallTimer(
        "GRADING", "glm-5.2", source_type="GRADING_TURN", source_id="s", attempt_no=1,
        trace_id="explicit-trace-1",
    )
    assert timer.trace_id == "explicit-trace-1"


def test_build_produces_a_real_ai_usage_that_validates():
    """ AiUsage 자체의 DB CHECK까지 실제로 통과하는 완전한 객체를 만드는지 """
    timer = LlmCallTimer("CODE_ANALYSIS", "z-ai/glm-5.2", source_type="CODE_MAP", source_id="job-1", attempt_no=1)
    with timer:
        pass
    entry = timer.build(input_token_count=100, output_token_count=20, cached_token_count=5, status="SUCCEEDED")
    assert entry.request_id
    assert entry.trace_id == "job-1"
    assert entry.feature_code == "CODE_ANALYSIS"
