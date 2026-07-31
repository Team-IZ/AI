""" Tier 1 -- 결정론적 랭커. 순수 함수만, 네트워크/파일시스템 없음.

D2 (2026-07-30): 이 모듈이 "후보 목록 -> 랭킹" 상태 전이 그 자체다. 같은 입력이면
언제 어디서 호출하든(스테이트풀 서버든 스테이트리스든) 항상 같은 출력이 나와야 한다는
게 이 모듈의 유일한 계약이다 -- 그래서 무작위성, 시계, 전역 캐시가 전혀 없다.

정렬키/타이브레이크/rank_evidence의 모양은 judgment/importance_rank.py(origin/
feat/poc_full)의 apply_rank()를 그대로 따른다 -- (rank_score, 2차 신호들..., path)
순으로 완전순서를 만들어 동점이 남지 않게 하고, tie_break_depth로 "어느 단계에서
갈렸는지"를 기록해 각 항목이 다른 항목과 대조하지 않고도 스스로 설명 가능하게 한다.

D13 (2026-07-31, 사용자 결정): 6번째 신호 curriculum -- 교안(teaches)/요구사항
(requirements)이 랭킹 **자체**에 반영된다(그 전까지는 analysis_doc.py(p05-3)가
"이미 뽑힌 파일에 대해 무엇을 쓸지"만 교안으로 정했고, 무엇을 뽑을지에는 교안이
전혀 관여하지 않았다).
  현재 상태(중요 -- 이 주석만 읽고 "교안이 순위를 움직인다"고 결론내지 말 것):
    이 신호는 **항상 계산되고 rank_evidence/결과 dict에 항상 기록되지만, 운영
    가중치는 0.0이라 순위를 움직이지 않는다**(codemap_weights.json의 provenance에
    실측 근거 전문). 실측에서 일부러 무의미하게 만든 교안이 파일의 20.4%에서
    발화하고 가중치 1.0일 때 상위 10위 중 4자리를 뒤바꿨다 -- '의미 있는 교안'과
    '무의미한 교안'을 구분하지 못하는 신호로 순위를 움직이면 poc_full의 Tier B
    (고정 키워드 위험 트리거)가 폐기된 것과 같은 실패를 반복하게 된다.
    오늘 교안이 실제로 순위에 반영되는 경로는 Tier 2다(crew.py D13).
  WHY 그런데도 Tier 1에 넣는가(가중치 0이면 없는 것과 같지 않은가): 아니다.
    (1) 가중치를 정하려면 먼저 그 신호가 실제 학생 제출물에서 무엇에 발화하는지가
        기록돼 있어야 한다 -- 이 신호가 PR-3 실측의 관측 장비 그 자체다
        (build_code_map_from_repo() 반환의 "curriculum" 블록).
    (2) Tier 2 프롬프트의 curriculum_hits가 이 값이다 -- 가중치 0이어도 Tier 2가
        "결정론적으로는 몇 건 겹쳤나"를 대조하는 데 쓴다(__init__.py D13).
    즉 가중치 0은 "이 코드가 죽어 있다"가 아니라 "관측은 하되 아직 판단에 쓰지
    않는다"이며, 켜는 순간은 데이터가 정한다(OPEN_QUESTIONS.md D13).
  WHY D2(순수성)를 안 깨는가: 이 신호의 입력은 호출자가 이미 갖고 있던 값
    (teaches/requirements)이고 계산은 토큰 집합 연산뿐이다 -- 무작위성/시계/전역
    캐시/네트워크가 새로 들어오지 않는다. curriculum.py는 tests/test_codemap_purity.py의
    PURE_MODULES에 추가돼 이 성질이 테스트로 고정된다.
  COST: 이건 의미 이해가 아니라 토큰 겹침이다(curriculum.py D13의 COST 참고).
    재현율이 낮고, 교안 문구와 코드 어휘가 다른 언어면(한글 교안 + 영문 코드)
    거의 발화하지 않는다. 정밀도 쪽으로 편향시킨 대신 감수한 값이다.
  기권 규칙(이 결정의 안전장치): curriculum_matches가 None이면 이 항은 분자에서만
    빠지는 게 아니라 **분모(weight_sum)에서도 빠진다**. 신호를 안 쓰는 호출은
    이 신호가 없던 시절과 rank_score까지 바이트 단위로 동일하다 -- 0.0으로 채워
    분모만 키우면 모든 점수가 5/6배로 조용히 바뀌어 "가중치를 안 켰는데 숫자가
    변했다"는 설명 불가능한 diff가 생긴다. 순서만 같으면 된다가 아니라 값도 같아야
    회귀 판정이 싸다(tests/test_codemap_rank.py::test_absent_curriculum_is_byte_identical).
  EXIT: 실측 결과 이 신호가 해로우면 codemap_weights.json의 curriculum을 0으로
    되돌리면 끝이다(weights.py 모듈 docstring의 "재보정 = 파일 교체 하나" 경로).
    가중치 0이면 분자 기여가 0이 되지만 분모에는 남아 점수 스케일이 바뀌므로,
    완전 원복이 필요하면 호출부에서 curriculum_matches=None으로 넘긴다.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from app.engines.codemap.models import CurriculumMatch, ImportGraph, RankedFile, RepoFile, Weights
from app.engines.shared.signals import AttributionSignal

# rank_evidence.terms에 넣지 않는 신호 키 -- 정규화 전 원시값이라 0..1 항이 아니다
# (fan_in_raw/curriculum_raw는 signals에는 남아 타이브레이크·프롬프트가 쓴다).
_RAW_ONLY_SIGNALS = frozenset({"fan_in_raw", "curriculum_raw"})

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
    curriculum_matches: Mapping[str, int] | None = None,
    max_curriculum_hits: int = 0,
) -> tuple[float, dict[str, float]]:
    """ 파일 하나의 가중합 점수와 정규화된 신호(0..1)들을 반환

    curriculum_matches가 None이면 curriculum 항 전체가 분자·분모 양쪽에서 빠진다
    (D13 기권 규칙) -- signals에도 키가 생기지 않는다.
    """
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

    signals = {
        "fan_in": fan_in,
        "fan_in_raw": float(fan_in_raw),
        "entry_point": entry_point,
        "path_depth": path_depth,
        "size": size,
        "own_commit": own_commit,
    }

    if curriculum_matches is not None:
        hits = curriculum_matches.get(f.path, 0)
        curriculum = (hits / max_curriculum_hits) if max_curriculum_hits > 0 else 0.0
        weight_sum += weights.curriculum
        weighted += weights.curriculum * curriculum
        signals["curriculum"] = curriculum
        signals["curriculum_raw"] = float(hits)

    score = weighted / weight_sum if weight_sum > 0 else 0.0
    return round(score, 6), signals


def _sort_key(path: str, score: float, signals: Mapping[str, float]) -> tuple:
    return (-score, -signals["fan_in_raw"], -signals["own_commit"], path)


def rank_files(
    files: Sequence[RepoFile],
    graph: ImportGraph,
    *,
    weights: Weights,
    attribution: Mapping[str, AttributionSignal] | None = None,
    curriculum: CurriculumMatch | None = None,
) -> tuple[RankedFile, ...]:
    """ 파일 목록 -> 완전히 정렬된 RankedFile 튜플. 입력을 변경하지 않는다(순수 함수)

    curriculum=None(기본값)이면 6번째 신호가 분자·분모 어디에도 참여하지 않아
    이 신호 도입 이전과 결과가 완전히 동일하다(D13 기권 규칙).
    """
    max_in_degree = max((graph.in_degree.get(f.path, 0) for f in files), default=0)

    curriculum_matches = curriculum.matches if curriculum is not None else None
    max_curriculum_hits = max(curriculum_matches.values(), default=0) if curriculum_matches else 0

    scored: list[tuple[str, float, dict[str, float]]] = []
    for f in files:
        score, signals = score_file(
            f, graph, attribution, weights, max_in_degree, curriculum_matches, max_curriculum_hits
        )
        scored.append((f.path, score, signals))

    scored.sort(key=lambda item: _sort_key(item[0], item[1], item[2]))

    # 실제로 분모에 들어간 가중치만 기록한다 -- rank_evidence는 "이 점수가 어떻게
    # 나왔는지"의 근거이지 가중치 파일의 사본이 아니다(D13 기권 규칙과 짝).
    weights_dict = {
        "fan_in": weights.fan_in, "entry_point": weights.entry_point,
        "path_depth": weights.path_depth, "size": weights.size, "own_commit": weights.own_commit,
    }
    if curriculum is not None:
        weights_dict["curriculum"] = weights.curriculum

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
            "terms": {k: v for k, v in signals.items() if k not in _RAW_ONLY_SIGNALS},
            "tie_break_depth": tie_break_depth,
        }
        ranked.append(RankedFile(path=path, rank=i + 1, rank_score=score, signals=signals, rank_evidence=rank_evidence))
        prev_key = key

    return tuple(ranked)
