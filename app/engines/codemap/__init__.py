""" codemap 스테이지의 조립부(composition root) -- Tier 1(결정론적) + 옵션 Tier 2(크루)

D2: 파일시스템(collect_repo_files, 가중치 JSON 읽기)과 네트워크(Tier 2)는 전부
여기서만 일어난다. 순수 모듈(graph/rank/shortlist/weights/ground)은 이 함수가
읽어들인 값을 인자로 받을 뿐, 스스로는 아무것도 읽지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.engines.codemap import collect, crew as crew_mod
from app.engines.codemap import curriculum as curriculum_mod
from app.engines.codemap import graph as graph_mod
from app.engines.codemap import ground as ground_mod
from app.engines.codemap import rank as rank_mod
from app.engines.codemap import shortlist as shortlist_mod
from app.engines.codemap import weights as weights_mod
from app.config import get_settings
from app.engines.codemap.models import CodeMapConfig, CollectLimits, CurriculumMatch, RankedFile, RepoFile
from app.engines.shared.budget import CallBudget, load_budget
from app.engines.shared.signals import AttributionSignal
from app.schemas.usage import AiUsage

_DEFAULT_WEIGHTS_PATH = Path(__file__).parent / "weights" / "codemap_weights.json"


def _build_candidates_block(ranked: Sequence[RankedFile], shortlist_paths: Sequence[str]) -> str:
    """ Tier 2 프롬프트의 candidates_block -- Tier 1이 이미 보여준 후보만, rank 순서대로

    D13: curriculum_hits는 Tier 1이 실제로 센 값이라 여기 같이 보여준다 -- 모델이
    "몇 건 걸렸는가"(결정론적 사실)와 "정말 관련이 있는가"(의미 판단)를 대조할 수
    있게 하기 위함이다. 0건이라고 관련이 없는 게 아니고(토큰이 안 겹쳤을 뿐),
    N건이라고 관련이 있는 것도 아니다 -- 그 판단이 Tier 2의 몫이다.
    """
    shortlist_set = set(shortlist_paths)
    lines = [
        f"- {rf.path} (rank={rf.rank}, score={rf.rank_score:.3f}, "
        f"fan_in={rf.signals.get('fan_in_raw', 0):.0f}, entry_point={rf.signals.get('entry_point', 0):.1f}"
        + (
            f", curriculum_hits={rf.signals['curriculum_raw']:.0f}"
            if "curriculum_raw" in rf.signals else ""
        )
        + ")"
        for rf in ranked
        if rf.path in shortlist_set
    ]
    return "\n".join(lines)


def _build_curriculum_block(
    teaches: Sequence[Mapping[str, Any]],
    requirements: Sequence[Mapping[str, Any]],
    curriculum: CurriculumMatch | None,
) -> str:
    """ Tier 2 프롬프트의 curriculum_block -- 교안/요구사항 원문 + Tier 1 토큰 매칭 진단

    D13: 이 블록이 Tier 2 프롬프트에서 유일하게 "Tier 1이 이미 계산한 것의 반복이
    아닌" 입력이다. 기존 candidates_block은 path/rank/score/fan_in처럼 Tier 1이
    이미 결론지은 값만 되풀이해서, 모델에게 새로 판단할 재료가 사실상 없었다.
    """
    lines: list[str] = []
    if teaches:
        lines.append("### 교안 개념(teaches)")
        for t in teaches:
            if not isinstance(t, Mapping):
                continue
            label = t.get("label") or t.get("canonicalName") or ""
            lines.append(f"- id={t.get('id', '')} unit={t.get('unitId', '')}: {label}")
    if requirements:
        lines.append("### 요구사항(requirements)")
        for r in requirements:
            if not isinstance(r, Mapping):
                continue
            rid = r.get("requirementId") or r.get("requirement_id") or ""
            lines.append(f"- id={rid}: {r.get('text', '')}")
    if not lines:
        return ""

    if curriculum is not None and curriculum.dropped_generic:
        lines.append(
            "### 참고: Tier 1이 변별력 없다고 판단해 토큰 매칭에서 제외한 단어"
            " (의미 판단에서까지 무시하라는 뜻은 아니다)"
        )
        lines.append("- " + ", ".join(curriculum.dropped_generic))
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
    teaches: Sequence[Mapping[str, Any]] | None = None,
    requirements: Sequence[Mapping[str, Any]] | None = None,
) -> dict:
    """ 저장소 경로 -> 코드 중요도 맵(dict). config.tier2_enabled가 True면 크루도 돈다.

    teaches/requirements를 주면 Tier 1의 6번째 신호(curriculum, rank.py D13)와
    Tier 2의 curriculum_block(crew.py D13) 양쪽에 반영된다. 생략하면 이 신호가
    없던 시절과 결과가 완전히 동일하다(D13 기권 규칙).

    반환 모양은 CLI(__main__.py)와 engine.py가 그대로 재사용한다:
    {"file_count", "ranked", "shortlist", "truncated", "files_by_path",
     "entries", "tier2_applied", "tier2_rejected", "curriculum", "ai_usage"}
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
    curriculum = curriculum_mod.match_curriculum(files, teaches, requirements)
    ranked = rank_mod.rank_files(
        files, import_graph, weights=w, attribution=attribution, curriculum=curriculum
    )

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
            curriculum_block=_build_curriculum_block(teaches or (), requirements or (), curriculum),
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
        # D13: 교안 신호가 실제로 무엇을 근거로 발화했는지 -- PR-3 실측(가중치 재보정)에
        # 필요한 유일한 원자료다. 신호가 기권했으면 None(키는 항상 있다).
        "curriculum": (
            {
                "item_count": curriculum.item_count,
                "matched_terms": list(curriculum.matched_terms),
                "dropped_generic": list(curriculum.dropped_generic),
                "dropped_short": list(curriculum.dropped_short),
                "matches": dict(curriculum.matches),
            }
            if curriculum is not None else None
        ),
        "ai_usage": ai_usage,
    }
