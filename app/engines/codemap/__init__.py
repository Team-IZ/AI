""" codemap 스테이지의 조립부(composition root) -- Phase 1: Tier 1(결정론적 랭커)만

D2: 파일시스템(collect_repo_files, 가중치 JSON 읽기)과 네트워크(Tier 2, Phase 4에서
추가)는 전부 여기서만 일어난다. 순수 모듈(graph/rank/shortlist/weights)은 이 함수가
읽어들인 값을 인자로 받을 뿐, 스스로는 아무것도 읽지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from app.engines.codemap import collect, graph as graph_mod, rank as rank_mod, shortlist as shortlist_mod, weights as weights_mod
from app.engines.codemap.models import CodeMapConfig, CollectLimits, RepoFile
from app.engines.shared.signals import AttributionSignal

_DEFAULT_WEIGHTS_PATH = Path(__file__).parent / "weights" / "codemap_weights.json"


def build_code_map_from_repo(
    repo_dir: str,
    *,
    config: CodeMapConfig | None = None,
    attribution: Mapping[str, AttributionSignal] | None = None,
    weights_path: Path | None = None,
) -> dict:
    """ 저장소 경로 -> Tier 1 랭킹 결과(dict). Tier 2는 Phase 4에서 config.tier2_enabled로 켠다.

    반환 모양은 CLI(__main__.py)와 향후 engine.py가 그대로 재사용한다:
    {"files": [...], "ranked": [...], "shortlist": [...], "truncated": [...]}
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
    }
