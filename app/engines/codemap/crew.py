""" Tier 2 -- 재랭킹. 순수 코어가 아니라 유일한 네트워크 edge(D2)

D1 (2026-07-31, crewai 제거): app.engines.shared.llm.chat()을 직접 호출한다(CrewAI 아님).
  WHY: 실측해 보니 이 스테이지의 실제 사용은 Agent 1개 + Task 1개 + tools=[](D12)
    뿐이었다 -- CrewAI가 실제로 제공하는 차별점(멀티에이전트 협업, 도구 사용)을
    이 스테이지는 애초에 하나도 쓰고 있지 않았다. 반면 crewai 패키지는 chromadb/
    pdfplumber/openpyxl/pyvis/auth0-python/opentelemetry-* 등 이 경로에서 전혀
    안 쓰이는 의존성을 ~1.9GB 끌고 온다(Cloudflare Container 이미지 빌드 중 실측,
    2026-07-31). analysis_doc.py(p05-3)가 이미 같은 이유로 chat() 직접 호출을
    쓰고 있어서, 이 스테이지도 그 패턴을 따르는 게 파이프라인 안에서 호출 방식이
    갈리던(D-old COST) 문제도 같이 없앤다.
  COST: crewai가 대신 해주던 재시도/파싱/토큰회계 등은 이미 shared.llm.chat()과
    LlmCallTimer가 동일하게 제공한다 -- 실질적 손실 없음. 유일한 진짜 손실은
    "crewAIInc/nvidia-demo와 같은 방식"이라는 원래 요구사항과의 정합성뿐이다.
  EXIT: 이 스테이지가 나중에 진짜 멀티라운드 도구 사용이 필요해지면(예: 재랭킹
    전에 실제로 다른 파일을 더 열어봐야 하는 경우), app.engines.shared.agent_loop
    (신규, 2026-07-31)의 run_tool_loop()로 옮긴다 -- CrewAI 재도입이 아니라 그
    모듈이 이미 이 목적으로 만들어졌다(D1 참고, agent_loop.py 모듈 docstring).
    requirements-codemap.txt는 참고용으로 남겨둔다(git 이력에도 crewai 기반
    구현이 남아 있음).

D13 (2026-07-31, 사용자 결정 "teaches/requirements가 랭킹 자체에 반영돼야 한다"):
  이 스테이지가 curriculum_block(교안 개념 + 요구사항 원문)을 실제 프롬프트 입력으로
  받는다. rank.py D13이 같은 결정의 Tier 1(결정론적 토큰 겹침) 절반이고, 이쪽이
  의미 판단 절반이다.
  WHY 여기가 의미 판단의 제자리인가: "이 파일이 '예외 처리' 교안과 관련 있는가"는
    토큰 겹침으로는 원리적으로 못 푼다(try/catch에는 '예외'라는 문자열이 없다).
    Tier 2는 이 파이프라인에서 유일하게 LLM을 부르는 지점이고, 이미 closed-vocabulary
    검증(ground.py)이 자유 서술 유출을 막고 있어서 새 판단 축을 추가하는 비용이
    가장 낮다.
  WHY 지금까지 Tier 2가 별 값을 못 했는가(이 변경의 진짜 동기): 기존 프롬프트 입력
    (candidates_block)은 path/rank/score/fan_in/entry_point뿐 -- 전부 Tier 1이 이미
    계산해서 결론까지 낸 값이다. 모델에게 "다시 판단할 새 재료"가 하나도 없었고,
    그래서 재랭킹은 사실상 Tier 1 점수를 되읽는 작업이었다. curriculum_block은
    Tier 1이 원리적으로 다룰 수 없는 종류의 정보를 처음으로 넣는다.
  COST: 프롬프트가 길어진다(truncation.curriculum_block=2000자로 상한). 교안/요구사항이
    없는 요청에서는 빈 문자열이라 기존과 동일한 프롬프트가 나간다 -- optional_placeholder라
    render()의 필수값 검사에도 걸리지 않는다.
  EXIT: 재랭킹 품질이 오히려 나빠지면 codemap_rerank.yaml에서 이 블록을 빼고
    manifest를 리빌드하면 끝이다(코드 변경 불필요 -- 값이 안 쓰이면 그만).
  범위 밖(의도적): tier2_enabled 기본값은 이 변경에서 건드리지 않았다. 여전히
    False이므로 이 스테이지 자체가 운영 기본 경로에서는 실행되지 않는다 --
    docs/code-importance-map/OPEN_QUESTIONS.md의 D13 참고(저장소 소유자 결정 대기).

D12(학생 코드는 읽기만, 절대 실행 안 함)는 이제 이 함수 하나로 지킨다: 프롬프트
  (candidates_block 등)에 필요한 내용을 이미 다 담아 보내므로 모델이 파일을 직접
  읽을 필요 자체가 없다 -- tools를 아예 안 보낸다(chat()의 tools 파라미터를
  아예 안 씀). allowed_paths에 ".git/"로 시작하는 경로가 하나라도 있으면 즉시
  assert로 막는 벨트-앤-브레이스도 그대로 유지한다.

D6(실패시 결정론적 랭킹으로 강등): 호출이 실패하면(타임아웃/레이트리밋/JSON 파싱
  실패 등) 빈 claims를 반환한다 -- merge_rerank(claims=())가 Tier 1 순서를 그대로
  보존하므로, 이 함수의 실패가 전체 job을 실패시키지 않는다.

p05-2(역할 미배정 파일 라벨링)는 아직 여기 안 붙인다 -- ground.py::parse_rerank가
  role을 claim의 필수 필드로 요구해서, "재랭킹은 안 하지만 라벨만 필요"인 항목을
  표현할 자리가 없다(CrewClaim에 role-only 상태가 없음). 억지로 끼워 맞추면
  검증 로직이 애매해지므로, 이 결정을 남겨두고 다음 확장 때 CodeMapEntry에
  role-only 경로를 추가하기로 한다.
"""
from __future__ import annotations

from typing import Callable

from app.engines.codemap.ground import parse_rerank
from app.engines.codemap.models import CrewClaim
from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import ChatResult, chat, classify_failure_code, extract_json_object
from app.engines.shared.prompts import load_stage, param_default, render
from app.engines.shared.timing import LlmCallTimer
from app.schemas.usage import AiUsage


def run_rerank_crew(
    *,
    candidates_block: str,
    allowed_paths: frozenset[str],
    model_code: str,
    budget: CallBudget,
    job_id: str,
    repo_summary_block: str = "",
    attribution_block: str = "",
    curriculum_block: str = "",
    chat_fn: Callable[..., ChatResult] = chat,
) -> tuple[tuple[CrewClaim, ...], tuple[str, ...], list[AiUsage]]:
    """ 반환: (채택된 CrewClaim들, ground.py가 거부한 사유들, AiUsage 목록)

    호출 실패/예산 소진 시 ((), (), [FAILED 기록 0~1건])을 반환한다 -- 빈 claims는
    merge_rerank에서 Tier 1 순서 그대로 보존으로 이어진다(D6).
    """
    assert not any(p.startswith(".git/") for p in allowed_paths), "D12 위반: .git 경로가 노출됨"

    ai_usage: list[AiUsage] = []
    if not candidates_block.strip() or budget.max_llm_calls < 1:
        return (), (), ai_usage

    stage = load_stage("p05", "p05-1")
    cand_limit = stage.truncation.get("candidates_block", len(candidates_block))
    summary_limit = stage.truncation.get("repo_summary_block", len(repo_summary_block))
    curriculum_limit = stage.truncation.get("curriculum_block", len(curriculum_block))
    values = {
        "candidates_block": candidates_block[:cand_limit],
        "repo_summary_block": repo_summary_block[:summary_limit],
        "attribution_block": attribution_block,
        "curriculum_block": curriculum_block[:curriculum_limit] or "(교안/요구사항 없음)",
    }
    messages = render(stage, values)
    max_tokens = param_default(stage, "max_tokens") or 2000
    temperature = param_default(stage, "temperature") or 0.0

    timer = LlmCallTimer(
        budget.feature_code, model_code, source_type=budget.source_type, source_id=job_id, attempt_no=1,
    )
    try:
        with timer:
            result = chat_fn(
                model_code=model_code, messages=messages, max_tokens=max_tokens, temperature=temperature,
                max_attempts=budget.max_attempts_per_call, timeout_s=budget.timeout_s,
            )
        parsed = extract_json_object(result.content)
    except Exception as exc:  # noqa: BLE001 -- D6: 모든 실패는 Tier 1로 강등, job을 안 죽인다
        ai_usage.append(timer.build(status="FAILED", failure_code=classify_failure_code(exc)))
        return (), (), ai_usage

    ai_usage.append(timer.build(
        input_token_count=result.input_tokens,
        output_token_count=result.output_tokens,
        cached_token_count=result.cached_tokens,
        status="SUCCEEDED",
    ))

    claims, rejected = parse_rerank(parsed, allowed_paths)
    return claims, rejected, ai_usage
