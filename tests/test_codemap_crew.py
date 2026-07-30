""" app/engines/codemap/crew.py -- Tier 2 재랭킹 테스트. 네트워크는 전혀 안 나간다

대부분의 테스트는 kickoff_fn을 페이크로 주입해 crewai 자체를 건드리지 않는다.
_default_kickoff()가 실제로 crewai.Agent/Task/Crew를 올바르게(특히 tools=[] --
D12) 구성하는지 확인하는 테스트만 진짜 crewai 패키지가 필요해서
pytest.importorskip로 감싼다 -- requirements-codemap.txt 없이 도는 CI의 기본
test job에서는 이 파일의 그 부분만 스킵된다.
"""
import pytest

from app.engines.codemap.crew import KickoffUsage, run_rerank_crew
from app.engines.shared.budget import CallBudget

try:
    import crewai  # noqa: F401
    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False

BUDGET = CallBudget(
    feature_code="CODE_ANALYSIS", source_type="CODE_MAP",
    max_llm_calls=8, max_tool_rounds=4, max_attempts_per_call=3, timeout_s=600,
)
ALLOWED = frozenset({"src/main.py", "src/util.py"})


def _fake_kickoff(json_text, usage=None):
    def kickoff_fn(*, system, user, model_code, max_tokens, temperature):
        return json_text, usage or KickoffUsage(prompt_tokens=100, completion_tokens=50, cached_prompt_tokens=0)
    return kickoff_fn


def test_returns_empty_when_candidates_block_blank():
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="   ", allowed_paths=ALLOWED, model_code="z-ai/glm-5.2",
        budget=BUDGET, job_id="job-1",
    )
    assert (claims, rejected, ai_usage) == ((), (), [])


def test_zero_budget_returns_empty_without_calling_kickoff():
    calls = {"n": 0}

    def kickoff_fn(**kwargs):
        calls["n"] += 1
        return "{}", KickoffUsage(0, 0)

    zero_budget = CallBudget(
        feature_code="CODE_ANALYSIS", source_type="CODE_MAP",
        max_llm_calls=0, max_tool_rounds=4, max_attempts_per_call=3, timeout_s=600,
    )
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="m",
        budget=zero_budget, job_id="job-1", kickoff_fn=kickoff_fn,
    )
    assert calls["n"] == 0
    assert (claims, rejected, ai_usage) == ((), (), [])


def test_successful_call_produces_claims_and_ai_usage():
    json_text = '{"changes": [{"path": "src/main.py", "role": "ENTRY_POINT", "delta_rank": 1, "reason_code": "ENTRY_POINT_CONFIRMED"}]}'
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="z-ai/glm-5.2",
        budget=BUDGET, job_id="job-1",
        kickoff_fn=_fake_kickoff(json_text, KickoffUsage(prompt_tokens=200, completion_tokens=30, cached_prompt_tokens=10)),
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


def test_kickoff_exception_falls_back_to_empty_claims_with_failed_ai_usage():
    def kickoff_fn(**kwargs):
        raise RuntimeError("network exploded")

    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", kickoff_fn=kickoff_fn,
    )
    assert claims == ()
    assert rejected == ()
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "FAILED"
    assert ai_usage[0].failure_code == "PROVIDER_ERROR"


def test_kickoff_timeout_error_maps_to_timeout_failure_code():
    from app.engines.shared.llm import LlmTimeoutError

    def kickoff_fn(**kwargs):
        raise LlmTimeoutError("took too long")

    _, _, ai_usage = run_rerank_crew(
        candidates_block="[x]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", kickoff_fn=kickoff_fn,
    )
    assert ai_usage[0].failure_code == "TIMEOUT"


def test_invalid_json_response_falls_back_gracefully():
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[main.py: rank 1]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", kickoff_fn=_fake_kickoff("this is not json at all"),
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
        budget=BUDGET, job_id="job-1", kickoff_fn=_fake_kickoff(json_text),
    )
    assert claims == ()
    assert rejected == ("UNKNOWN_PATH",)
    assert ai_usage[0].status == "SUCCEEDED"  # 호출은 성공, 내용만 거부됨


def test_asserts_git_paths_never_reach_crew():
    """ D12 벨트-앤-브레이스: .git/ 경로가 allowed_paths에 있으면 즉시 assert로 막힌다 """
    with pytest.raises(AssertionError, match="D12"):
        run_rerank_crew(
            candidates_block="[x]", allowed_paths=frozenset({".git/HEAD"}), model_code="m",
            budget=BUDGET, job_id="job-1", kickoff_fn=_fake_kickoff("{}"),
        )


def test_truncates_candidates_block_per_stage_truncation_config():
    """ prompt_manifest.json의 p05-1 truncation.candidates_block(12000)을 실제로 지키는지 """
    captured = {}

    def kickoff_fn(*, system, user, model_code, max_tokens, temperature):
        captured["user"] = user
        return '{"changes": []}', KickoffUsage(1, 1)

    huge_block = "x" * 20_000
    run_rerank_crew(
        candidates_block=huge_block, allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-1", kickoff_fn=kickoff_fn,
    )
    # 잘린 12000자만 user_template에 들어가야 한다(20000자 전체가 그대로 들어가면 안 됨)
    assert huge_block[:12000] in captured["user"]
    assert huge_block not in captured["user"]


def test_uses_budget_feature_code_and_source_type_in_ai_usage():
    claims, rejected, ai_usage = run_rerank_crew(
        candidates_block="[x]", allowed_paths=ALLOWED, model_code="m",
        budget=BUDGET, job_id="job-42", kickoff_fn=_fake_kickoff('{"changes": []}'),
    )
    assert ai_usage[0].source_id == "job-42"
    assert ai_usage[0].idempotency_key == "job-42:CODE_MAP:1"


@pytest.mark.skipif(not HAS_CREWAI, reason="crewai not installed (requirements-codemap.txt)")
class TestDefaultKickoffCrewAiWiring:
    """ 실제 crewai 패키지가 있어야 의미 있는 테스트 -- 코드맵 extras(requirements-codemap.txt)
    없이 도는 CI job에서는 이 클래스만 스킵된다(나머지 fake-kickoff 테스트는 그대로 돈다). """

    def test_default_kickoff_never_attaches_any_tools(self, monkeypatch):
        """ D12의 핵심 보장: _default_kickoff가 만드는 Agent에 tools가 절대 안 붙는다 """
        from crewai import Crew

        captured_agents = []
        original_init = Crew.__init__

        def spy_init(self, *args, **kwargs):
            captured_agents.extend(kwargs.get("agents", []))
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(Crew, "__init__", spy_init)

        class FakeUsage:
            prompt_tokens = 10
            completion_tokens = 5
            cached_prompt_tokens = 0

        class FakeOutput:
            raw = '{"changes": []}'
            token_usage = FakeUsage()

        monkeypatch.setattr(Crew, "kickoff", lambda self, *a, **kw: FakeOutput())
        monkeypatch.setattr("app.engines.codemap.crew.nvidia_api_key", lambda: "test-key")

        from app.engines.codemap.crew import _default_kickoff

        _default_kickoff(system="sys", user="usr", model_code="z-ai/glm-5.2", max_tokens=100, temperature=0.0)

        assert len(captured_agents) == 1
        assert captured_agents[0].tools == []

    def test_default_kickoff_returns_parsed_usage(self, monkeypatch):
        from crewai import Crew

        class FakeUsage:
            prompt_tokens = 77
            completion_tokens = 33
            cached_prompt_tokens = 5

        class FakeOutput:
            raw = '{"changes": []}'
            token_usage = FakeUsage()

        monkeypatch.setattr(Crew, "kickoff", lambda self, *a, **kw: FakeOutput())
        monkeypatch.setattr("app.engines.codemap.crew.nvidia_api_key", lambda: "test-key")

        from app.engines.codemap.crew import _default_kickoff

        raw, usage = _default_kickoff(system="sys", user="usr", model_code="m", max_tokens=100, temperature=0.0)
        assert raw == '{"changes": []}'
        assert usage.prompt_tokens == 77
        assert usage.completion_tokens == 33
        assert usage.cached_prompt_tokens == 5
