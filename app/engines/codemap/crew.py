""" Tier 2 -- CrewAI 재랭킹. 순수 코어가 아니라 유일한 네트워크 edge(D2)

crewai는 함수 안에서만 지연 import한다 -- requirements-codemap.txt를 설치하지
않아도 이 모듈 자체는 import 가능해야 한다(engine_mode=codemap이라도 tier2_enabled
=False면 이 함수가 아예 호출되지 않으므로).

D12(학생 코드는 읽기만, 절대 실행 안 함)를 두 겹으로 지킨다:
  1) Agent에 tools를 전혀 주지 않는다 -- crewai_tools의 FileReadTool/
     DirectoryReadTool은 호스트의 임의 경로를 읽을 수 있어(~/nvidia-demo 원본
     파이프라인이 실제로 그렇게 씀) 신뢰 안 되는 학생 코드에 붙이기에 안전하지
     않다. 프롬프트(candidates_block 등)에 필요한 내용을 이미 다 담아 보내므로
     에이전트가 파일을 직접 읽을 필요 자체가 없다.
  2) allowed_paths에 ".git/"로 시작하는 경로가 하나라도 있으면 즉시 assert로
     막는다 -- Tier 1이 이미 .git을 스킵하지만(collect.py), 이 함수가 다른
     경로로도 호출될 가능성에 대비한 벨트-앤-브레이스.

D6(실패시 결정론적 랭킹으로 강등): 크루 호출이 실패하면(타임아웃/키풀고갈/
JSON 파싱 실패 등) 빈 claims를 반환한다 -- merge_rerank(claims=())가
Tier 1 순서를 그대로 보존하므로, 이 함수의 실패가 전체 job을 실패시키지 않는다.

p05-2(역할 미배정 파일 라벨링)는 아직 여기 안 붙인다 -- ground.py::parse_rerank가
role을 claim의 필수 필드로 요구해서, "재랭킹은 안 하지만 라벨만 필요"인 항목을
표현할 자리가 없다(CrewClaim에 role-only 상태가 없음). 억지로 끼워 맞추면
검증 로직이 애매해지므로, 이 결정을 남겨두고 다음 확장 때 CodeMapEntry에
role-only 경로를 추가하기로 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from app.engines.codemap.ground import parse_rerank
from app.engines.codemap.models import CrewClaim
from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import extract_json_object
from app.engines.shared.prompts import load_stage, param_default, render
from app.engines.shared.secrets import nvidia_api_key
from app.engines.shared.timing import LlmCallTimer
from app.schemas.common import AiUsageEntry

NVIDIA_LITELLM_BASE_URL = "https://integrate.api.nvidia.com/v1"

_KNOWN_FAILURE_CODES = {"TIMEOUT", "RATE_LIMITED", "PROVIDER_ERROR", "INVALID_JSON", "CONTEXT_OVERFLOW"}


@dataclass(frozen=True)
class KickoffUsage:
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int = 0


class KickoffFn(Protocol):
    def __call__(
        self, *, system: str, user: str, model_code: str, max_tokens: int, temperature: float
    ) -> tuple[str, KickoffUsage]: ...


def _default_kickoff(*, system: str, user: str, model_code: str, max_tokens: int, temperature: float) -> tuple[str, KickoffUsage]:
    """ 실제 CrewAI Agent/Task/Crew 구성 -- 이 함수 안에서만 crewai를 import한다 """
    from crewai import LLM, Agent, Crew, Task  # 지연 import -- 위 모듈 docstring 참고

    llm = LLM(
        model=f"nvidia_nim/{model_code}",
        api_key=nvidia_api_key(),
        base_url=NVIDIA_LITELLM_BASE_URL,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    agent = Agent(
        role="Code Importance Reviewer",
        goal=system,
        backstory="You review shortlisted source files for a code-ownership assessment tool.",
        llm=llm,
        tools=[],  # D12 -- 파일 접근 도구를 절대 주지 않는다(위 모듈 docstring 참고)
        verbose=False,
    )
    task = Task(description=user, expected_output="A single JSON object, nothing else.", agent=agent)
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    result = crew.kickoff()
    usage = result.token_usage
    return result.raw, KickoffUsage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
    )


def _classify_failure(exc: Exception) -> str:
    code = getattr(exc, "failure_code", None)
    return code if code in _KNOWN_FAILURE_CODES else "PROVIDER_ERROR"


def run_rerank_crew(
    *,
    candidates_block: str,
    allowed_paths: frozenset[str],
    model_code: str,
    budget: CallBudget,
    job_id: str,
    repo_summary_block: str = "",
    attribution_block: str = "",
    kickoff_fn: Callable[..., tuple[str, KickoffUsage]] = _default_kickoff,
) -> tuple[tuple[CrewClaim, ...], tuple[str, ...], list[AiUsageEntry]]:
    """ 반환: (채택된 CrewClaim들, ground.py가 거부한 사유들, AiUsageEntry 목록)

    호출 실패/예산 소진 시 ((), (), [FAILED 기록 0~1건])을 반환한다 -- 빈 claims는
    merge_rerank에서 Tier 1 순서 그대로 보존으로 이어진다(D6).
    """
    assert not any(p.startswith(".git/") for p in allowed_paths), "D12 위반: .git 경로가 크루에 노출됨"

    ai_usage: list[AiUsageEntry] = []
    if not candidates_block.strip() or budget.max_llm_calls < 1:
        return (), (), ai_usage

    stage = load_stage("p05", "p05-1")
    cand_limit = stage.truncation.get("candidates_block", len(candidates_block))
    summary_limit = stage.truncation.get("repo_summary_block", len(repo_summary_block))
    values = {
        "candidates_block": candidates_block[:cand_limit],
        "repo_summary_block": repo_summary_block[:summary_limit],
        "attribution_block": attribution_block,
    }
    messages = render(stage, values)
    max_tokens = param_default(stage, "max_tokens") or 2000
    temperature = param_default(stage, "temperature") or 0.0

    timer = LlmCallTimer(
        budget.feature_code, model_code, source_type=budget.source_type, source_id=job_id, attempt_no=1,
    )
    try:
        with timer:
            raw_text, usage = kickoff_fn(
                system=messages[0]["content"], user=messages[1]["content"],
                model_code=model_code, max_tokens=max_tokens, temperature=temperature,
            )
        parsed = extract_json_object(raw_text)
    except Exception as exc:  # noqa: BLE001 -- D6: Tier 2의 모든 실패는 Tier 1로 강등, job을 안 죽인다
        ai_usage.append(timer.build(status="FAILED", failure_code=_classify_failure(exc)))
        return (), (), ai_usage

    ai_usage.append(timer.build(
        input_token_count=usage.prompt_tokens,
        output_token_count=usage.completion_tokens,
        cached_token_count=usage.cached_prompt_tokens,
        status="SUCCEEDED",
    ))

    claims, rejected = parse_rerank(parsed, allowed_paths)
    return claims, rejected, ai_usage
