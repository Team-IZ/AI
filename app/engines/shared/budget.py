""" LLM 호출/도구 라운드 예산 -- 매직넘버를 코드에 박지 않고 파일 하나로 분리(D6)

judgment/rank_weights/rank_weights.json(origin/feat/poc_full)과 같은 패턴:
데이터(이 JSON)와 로직(crew.py의 예산 카운팅)을 분리해 재보정을 파일 교체
하나로 끝낸다. max_llm_calls와 max_tool_rounds를 일부러 별개 필드로 둔다 --
도구 호출(파일 읽기 등 mid-reasoning) 1건과 실제 모델 완성 호출 1건을 하나의
숫자로 뭉개면 비용/지연 분석이 어긋난다는 게 D8의 지적이고, D8은 아직
결론이 안 났다(docs/code-importance-map/OPEN_QUESTIONS.md). 이 분리 자체는
결론이 나기 전에도 비용이 없으므로 지금 해 둔다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_BUDGETS_PATH = Path(__file__).parent / "budgets" / "llm_budgets.json"

_DEFAULT_BUDGETS = {
    "CODE_MAP": {
        "feature_code": "CODE_ANALYSIS",
        "source_type": "CODE_MAP",
        "max_llm_calls": 8,
        "max_tool_rounds": 4,
        "max_attempts_per_call": 3,
        "timeout_s": 600,
    },
}


@dataclass(frozen=True)
class CallBudget:
    feature_code: str
    source_type: str
    max_llm_calls: int
    max_tool_rounds: int
    max_attempts_per_call: int
    timeout_s: float


def _budget_from_dict(key: str, raw: dict) -> CallBudget:
    return CallBudget(
        feature_code=raw["feature_code"],
        source_type=raw["source_type"],
        max_llm_calls=raw["max_llm_calls"],
        max_tool_rounds=raw["max_tool_rounds"],
        max_attempts_per_call=raw["max_attempts_per_call"],
        timeout_s=raw["timeout_s"],
    )


def load_budget(key: str, *, path: Path | None = None) -> CallBudget:
    """ path를 생략하면 app/engines/shared/budgets/llm_budgets.json을 읽는다.
    파일이 없거나 깨졌거나 key가 없으면 _DEFAULT_BUDGETS의 같은 이름 값으로 폴백. """
    target = path or _BUDGETS_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        raw = data.get("budgets", {})[key]
    except (OSError, ValueError, KeyError):
        raw = _DEFAULT_BUDGETS.get(key)
        if raw is None:
            raise KeyError(f"알 수 없는 budget key: {key} (파일에도, 기본값에도 없음)") from None
    return _budget_from_dict(key, raw)
