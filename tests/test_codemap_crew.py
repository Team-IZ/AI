""" app/engines/codemap/crew.py -- Tier 2 재랭킹 테스트. 네트워크는 전혀 안 나간다

crewai 제거(2026-07-31, crew.py 모듈 docstring D1) 이후로는 chat_fn을 페이크로
주입해 analysis_doc.py 테스트와 동일한 패턴을 쓴다.
"""
from app.engines.codemap.crew import run_rerank_crew
from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import ChatResult

BUDGET = CallBudget(
    feature_code="CODE_ANALYSIS", source_type="CODE_MAP",
    max_llm_calls=8, max_tool_rounds=4, max_attempts_per_call=3, timeout_s=600,
)
ALLOWED = frozenset({"src/main.py", "src/util.py"})


def _fake_chat(json_text, *, input_tokens=100, output_tokens=50, cached_tokens=0):
    def chat_fn(**kwargs):
        return ChatResult(
            content=json_text, finish_reason="stop",
            input_tokens=input_tokens, output_tokens=output_tokens, cached_tokens=cached_tokens,
        )
    return chat_fn


def test_returns_empty_when_candidates_block_blank():
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="   ", allowed_paths=ALLOWED, model_code="z-ai/glm-5.2",
        budget=BUDGET, job_id="job-1",
    )
    assert (claims, rejected, ai_usage) == ((), (), [])


def test_zero_budget_returns_empty_without_calling_chat():
    calls = {"n": 0}

    def chat_fn(**kwargs):
        calls["n"] += 1
        return ChatResult(content="{}", finish_reason="stop", input_tokens=0, output_tokens=0, cached_tokens=0)

    zero_budget = CallBudget(
        feature_code="CODE_ANALYSIS", source_type="CODE_MAP",
        max_llm_calls=0, max_tool_rounds=4, max_attempts_per_call=3, timeout_s=600,
    )
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="m",
        budget=zero_budget, job_id="job-1", chat_fn=chat_fn,
    )
    assert calls["n"] == 0
    assert (claims, rejected, ai_usage) == ((), (), [])


def test_successful_call_produces_claims_and_ai_usage():
    json_text = '{"changes": [{"path": "src/main.py", "role": "ENTRY_POINT", "delta_rank": 1, "reason_code": "ENTRY_POINT_CONFIRMED"}]}'
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="z-ai/glm-5.2",
        budget=BUDGET, job_id="job-1",
        chat_fn=_fake_chat(json_text, input_tokens=200, output_tokens=30, cached_tokens=10),
    )
    assert len(claims) == 1
    assert claims[0].path == "src/main.py"
    assert rejected == ()
    assert len(ai_usage) == 1
    entry = ai_usage[0]
    assert entry.status == "SUCCEEDED"
    assert entry.input_token_count == 200
    assert entry.output_token_count == 30
    assert entry.cached_token_count == 10
    assert entry.feature_code == "CODE_ANALYSIS"
    assert entry.source_type == "CODE_MAP"
    assert entry.latency_ms >= 0


def test_chat_exception_falls_back_to_empty_claims_with_failed_ai_usage():
    def chat_fn(**kwargs):
        raise RuntimeError("network exploded")

    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert claims == ()
    assert rejected == ()
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "FAILED"
    assert ai_usage[0].failure_code == "PROVIDER_ERROR"


def test_chat_timeout_error_maps_to_timeout_failure_code():
    from app.engines.shared.llm import LlmTimeoutError

    def chat_fn(**kwargs):
        raise LlmTimeoutError("took too long")

    _, _, ai_usage = run_rerank_crew(
        candidates_block="[x]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert ai_usage[0].failure_code == "TIMEOUT"


def test_invalid_json_response_falls_back_gracefully():
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", chat_fn=_fake_chat("this is not json at all"),
    )
    assert claims == ()
    assert ai_usage[0].status == "FAILED"
    assert ai_usage[0].failure_code == "INVALID_JSON"


def test_ground_rejection_flows_through_unchanged():
    """ 모델이 후보 목록 밖 경로를 주장하면 claims는 비지만 ai_usage는 SUCCEEDED로 남는다
    (호출 자체는 성공했다 -- 검증 실패는 별개의 일). """
    json_text = '{"changes": [{"path": "src/not_in_list.py", "role": "UTIL", "delta_rank": 1, "reason_code": "IMPORT_HUB"}]}'
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", chat_fn=_fake_chat(json_text),
    )
    assert claims == ()
    assert rejected == ("UNKNOWN_PATH",)
    assert ai_usage[0].status == "SUCCEEDED"  # 호출은 성공, 내용만 거부됨


def test_asserts_git_paths_never_reach_the_call():
    """ D12 벨트-앤-브레이스: .git/ 경로가 allowed_paths에 있으면 즉시 assert로 막힌다 """
    import pytest

    with pytest.raises(AssertionError, match="D12"):
        run_rerank_crew(
            candidates_block="[x]", allowed_paths=frozenset({".git/HEAD"}), model_code="m",
            budget=BUDGET, job_id="job-1", chat_fn=_fake_chat("{}"),
        )


def test_truncates_candidates_block_per_stage_truncation_config():
    """ prompt_manifest.json의 p05-1 truncation.candidates_block(12000)을 실제로 지키는지 """
    captured = {}

    def chat_fn(*, messages, **kwargs):
        captured["user"] = messages[1]["content"]
        return ChatResult(content='{"changes": []}', finish_reason="stop", input_tokens=1, output_tokens=1, cached_tokens=0)

    huge_block = "x" * 20_000
    run_rerank_crew(
        candidates_block=huge_block, allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    # 잘린 12000자만 user_template에 들어가야 한다(20000자 전체가 그대로 들어가면 안 됨)
    assert huge_block[:12000] in captured["user"]
    assert huge_block not in captured["user"]


def test_uses_budget_feature_code_and_source_type_in_ai_usage():
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[x]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-42", chat_fn=_fake_chat('{"changes": []}'),
    )
    assert ai_usage[0].source_id == "job-42"
    assert ai_usage[0].idempotency_key == "job-42:CODE_MAP:1"
