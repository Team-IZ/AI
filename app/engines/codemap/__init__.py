""" codemap 스테이지의 조립부(composition root) -- Tier 1(결정론적) + 옵션 Tier 2(크루)

D2: 파일시스템(collect_repo_files, 가중치 JSON 읽기)과 네트워크(Tier 2)는 전부
여기서만 일어난다. 순수 모듈(graph/rank/shortlist/weights/ground)은 이 함수가
읽어들인 값을 인자로 받을 뿐, 스스로는 아무것도 읽지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.engines.codemap import collect, crew as crew_mod
from app.engines.codemap import graph as graph_mod
from app.engines.codemap import ground as ground_mod
from app.engines.codemap import rank as rank_mod
from app.engines.codemap import shortlist as shortlist_mod
from app.engines.codemap import weights as weights_mod
from app.config import get_settings
from app.engines.codemap.models import CodeMapConfig, CollectLimits, RankedFile, RepoFile
from app.engines.shared.budget import CallBudget, load_budget
from app.engines.shared.signals import AttributionSignal
from app.schemas.usage import AiUsage

_DEFAULT_WEIGHTS_PATH = Path(__file__).parent / "weights" / "codemap_weights.json"


def _build_candidates_block(ranked: Sequence[RankedFile], shortlist_paths: Sequence[str]) -> str:
    """ Tier 2 프롬프트의 candidates_block -- Tier 1이 이미 보여준 후보만, rank 순서대로 """
    shortlist_set = set(shortlist_paths)
    lines = [
        f"- {rf.path} (rank={rf.rank}, score={rf.rank_score:.3f}, "
        f"fan_in={rf.signals.get('fan_in_raw', 0):.0f}, entry_point={rf.signals.get('entry_point', 0):.1f})"
        for rf in ranked
        if rf.path in shortlist_set
    ]
    return "\n".join(lines)


def build_code_map_from_repo(
    repo_dir: str,
    *,
    config: CodeMapConfig | None = None,
    attribution: Mapping[str, AttributionSignal] | None = None,
    weights_path: Path | None = None,
    job_id: str = "unknown",
    budget: CallBudget | None = None,
    crew_chat_fn: Callable[..., Any] | None = None,
) -> dict:
    """ 저장소 경로 -> 코드 중요도 맵(dict). config.tier2_enabled가 True면 크루도 돈다.

    반환 모양은 CLI(__main__.py)와 engine.py가 그대로 재사용한다:
    {"file_count", "ranked", "shortlist", "truncated", "files_by_path",
     "entries", "tier2_applied", "tier2_rejected", "ai_usage"}
    """
    config = config or CodeMapConfig()
    path = weights_path or _DEFAULT_WEIGHTS_PATH
    try:
        json_text = path.read_text(encoding="utf-8")
    except OSError:
        json_text = None
    w = weights_mod.parse_weights(json_text)

    files: tuple[RepoFile, ...] = collect.collect_repo_files(repo_dir, limits=CollectLimits())
    import_graph = graph_mod.build_import_graph(files)
    ranked = rank_mod.rank_files(files, import_graph, weights=w, attribution=attribution)

    files_by_path = {f.path: f for f in files}
    selected, truncated = shortlist_mod.select_shortlist(
        ranked, files_by_path, max_files=config.max_shortlist_files, max_chars=config.max_shortlist_chars
    )
    selected_set = set(selected)
    shortlisted_ranked = tuple(rf for rf in ranked if rf.path in selected_set)

    ai_usage: list[AiUsage] = []
    tier2_rejected: tuple[str, ...] = ()
    claims: tuple = ()
    if config.tier2_enabled and selected:
        effective_budget = budget or load_budget("CODE_MAP")
        chat_kwargs = {"chat_fn": crew_chat_fn} if crew_chat_fn is not None else {}
        claims, tier2_rejected, call_usage = crew_mod.run_rerank_crew(
            candidates_block=_build_candidates_block(ranked, selected),
            allowed_paths=frozenset(selected),
            model_code=config.model_code or get_settings().default_model_code,
            budget=effective_budget,
            job_id=job_id,
            **chat_kwargs,
        )
        ai_usage.extend(call_usage)

    entries = ground_mod.merge_rerank(shortlisted_ranked, claims, max_rank_shift=config.max_rank_shift)

    return {
        "file_count": len(files),
        "ranked": [
            {
                "path": rf.path,
                "rank": rf.rank,
                "rank_score": rf.rank_score,
                "signals": dict(rf.signals),
                "rank_evidence": dict(rf.rank_evidence),
            }
            for rf in ranked
        ],
        "shortlist": list(selected),
        "truncated": list(truncated),
        "files_by_path": {path: f.text for path, f in files_by_path.items()},
        "entries": list(entries),
        "tier2_applied": bool(claims),
        "tier2_rejected": list(tier2_rejected),
        "ai_usage": ai_usage,
    }
