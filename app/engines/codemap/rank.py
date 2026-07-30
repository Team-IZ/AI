""" Tier 1 -- 결정론적 랭커. 순수 함수만, 네트워크/파일시스템 없음.

D2 (2026-07-30): 이 모듈이 "후보 목록 -> 랭킹" 상태 전이 그 자체다. 같은 입력이면
언제 어디서 호출하든(스테이트풀 서버든 스테이트리스든) 항상 같은 출력이 나와야 한다는
게 이 모듈의 유일한 계약이다 -- 그래서 무작위성, 시계, 전역 캐시가 전혀 없다.

정렬키/타이브레이크/rank_evidence의 모양은 judgment/importance_rank.py(origin/
feat/poc_full)의 apply_rank()를 그대로 따른다 -- (rank_score, 2차 신호들..., path)
순으로 완전순서를 만들어 동점이 남지 않게 하고, tie_break_depth로 "어느 단계에서
갈렸는지"를 기록해 각 항목이 다른 항목과 대조하지 않고도 스스로 설명 가능하게 한다.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from app.engines.codemap.models import ImportGraph, RankedFile, RepoFile, Weights
from app.engines.shared.signals import AttributionSignal

ENTRY_POINT_STEMS = {
    "main", "index", "app", "routes", "server", "__main__", "application", "program",
}

# 매우 작은 파일(배럴 재수출 등)과 매우 큰 파일(생성된 코드일 가능성) 둘 다 낮게 평가하는
# plateau 함수의 경계값. 실측 데이터 없이 시작하는 값이라 provenance로 표시한다(D6과 같은 원칙).
_SIZE_TINY_LINES = 5
_SIZE_PLATEAU_HIGH = 400
_SIZE_LARGE_LINES = 2000

_OWN_COMMIT_WEIGHT_BY_TYPE = {
    "AUTHORED": 1.0,
    "MODIFIED": 0.6,
    "UNTOUCHED": 0.0,
    "UNKNOWN": 0.0,
}


def _entry_point_signal(f: RepoFile, graph: ImportGraph) -> float:
    stem = f.path.rsplit("/", 1)[-1]
    dot = stem.rfind(".")
    if dot > 0:
        stem = stem[:dot]
    if stem.lower() in ENTRY_POINT_STEMS:
        return 1.0
    # import 그래프에서 아무도 참조하지 않는데 자신은 뭔가를 참조하는 파일 -- 약한 진입점 신호
    if graph.in_degree.get(f.path, 0) == 0 and graph.out_degree.get(f.path, 0) > 0:
        return 0.6
    return 0.0


def _size_signal(line_count: int) -> float:
    """ 아주 작은 파일(재수출 배럴)과 아주 큰 파일(생성된 코드) 둘 다 낮게, 중간을 높게 """
    if line_count <= _SIZE_TINY_LINES:
        return line_count / _SIZE_TINY_LINES if _SIZE_TINY_LINES else 0.0
    if line_count <= _SIZE_PLATEAU_HIGH:
        return 1.0
    if line_count >= _SIZE_LARGE_LINES:
        return 0.1
    span = _SIZE_LARGE_LINES - _SIZE_PLATEAU_HIGH
    return max(0.1, 1.0 - 0.9 * (line_count - _SIZE_PLATEAU_HIGH) / span)


def _own_commit_signal(path: str, attribution: Mapping[str, AttributionSignal] | None) -> float:
    if not attribution:
        return 0.0
    sig = attribution.get(path)
    if sig is None:
        return 0.0
    return _OWN_COMMIT_WEIGHT_BY_TYPE.get(sig.attribution_type, 0.0) * sig.confidence


def score_file(
    f: RepoFile,
    graph: ImportGraph,
    attribution: Mapping[str, AttributionSignal] | None,
    weights: Weights,
    max_in_degree: int,
) -> tuple[float, dict[str, float]]:
    """ 파일 하나의 가중합 점수와 정규화된 신호(0..1)들을 반환 """
    fan_in_raw = graph.in_degree.get(f.path, 0)
    fan_in = (fan_in_raw / max_in_degree) if max_in_degree > 0 else 0.0
    entry_point = _entry_point_signal(f, graph)
    depth = f.path.count("/")
    path_depth = 1.0 / (1.0 + depth)
    size = _size_signal(f.line_count)
    own_commit = _own_commit_signal(f.path, attribution)

    weight_sum = weights.fan_in + weights.entry_point + weights.path_depth + weights.size + weights.own_commit
    weighted = (
        weights.fan_in * fan_in
        + weights.entry_point * entry_point
        + weights.path_depth * path_depth
        + weights.size * size
        + weights.own_commit * own_commit
    )
    score = weighted / weight_sum if weight_sum > 0 else 0.0

    signals = {
        "fan_in": fan_in,
        "fan_in_raw": float(fan_in_raw),
        "entry_point": entry_point,
        "path_depth": path_depth,
        "size": size,
        "own_commit": own_commit,
    }
    return round(score, 6), signals


def _sort_key(path: str, score: float, signals: Mapping[str, float]) -> tuple:
    return (-score, -signals["fan_in_raw"], -signals["own_commit"], path)


def rank_files(
    files: Sequence[RepoFile],
    graph: ImportGraph,
    *,
    weights: Weights,
    attribution: Mapping[str, AttributionSignal] | None = None,
) -> tuple[RankedFile, ...]:
    """ 파일 목록 -> 완전히 정렬된 RankedFile 튜플. 입력을 변경하지 않는다(순수 함수) """
    max_in_degree = max((graph.in_degree.get(f.path, 0) for f in files), default=0)

    scored: list[tuple[str, float, dict[str, float]]] = []
    for f in files:
        score, signals = score_file(f, graph, attribution, weights, max_in_degree)
        scored.append((f.path, score, signals))

    scored.sort(key=lambda item: _sort_key(item[0], item[1], item[2]))

    weights_dict = {
        "fan_in": weights.fan_in, "entry_point": weights.entry_point,
        "path_depth": weights.path_depth, "size": weights.size, "own_commit": weights.own_commit,
    }

    ranked: list[RankedFile] = []
    prev_key: tuple | None = None
    for i, (path, score, signals) in enumerate(scored):
        key = _sort_key(path, score, signals)
        if prev_key is None:
            tie_break_depth = 0
        else:
            tie_break_depth = next((d for d in range(len(key)) if key[d] != prev_key[d]), len(key))
        rank_evidence = {
            "weights": weights_dict,
            "terms": {k: v for k, v in signals.items() if k != "fan_in_raw"},
            "tie_break_depth": tie_break_depth,
        }
        ranked.append(RankedFile(path=path, rank=i + 1, rank_score=score, signals=signals, rank_evidence=rank_evidence))
        prev_key = key

    return tuple(ranked)
