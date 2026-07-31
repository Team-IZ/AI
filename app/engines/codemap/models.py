""" codemap 스테이지의 값 객체. 전부 @dataclass(frozen=True) -- D2 순수성의 기반

D2 (2026-07-30): 상태 전이(후보 목록 -> 랭킹 -> 최종 선정)를 순수 함수로 만든다는
결정의 핵심은 "입력이 실수로 바뀌지 않는다"는 보장이다. list 대신 tuple을 쓰고
frozen=True로 고정하면 실수로 변경하려는 시도 자체가 TypeError로 즉시 드러난다
(조용한 버그가 아니라 바로 터지는 버그가 된다). 이 모듈은 순수 모듈(graph/rank/
shortlist/ground)만 import한다 -- os/pathlib/시간/네트워크는 여기 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.engines.shared.signals import AttributionSignal


@dataclass(frozen=True)
class RepoFile:
    """ 저장소에서 수집된 소스 파일 하나. collect.py(불순 -- 파일시스템 I/O)가 만든다 """

    path: str  # repo 루트 기준 상대경로, '/' 구분자로 정규화됨
    ext: str
    size_bytes: int
    line_count: int
    text: str


@dataclass(frozen=True)
class ImportGraph:
    """ (src, dst) 간선의 dedup된 집합 + 집계된 fan_in/fan_out.

    D12(원본 저장소의 fan-in 이중계산 버그) 재발 방지: edges는 반드시 set으로
    dedupe한 뒤 집계해야 한다 -- graph.py::build_import_graph()의 책임.
    """

    edges: tuple[tuple[str, str], ...]
    in_degree: Mapping[str, int]
    out_degree: Mapping[str, int]


@dataclass(frozen=True)
class Weights:
    fan_in: float
    entry_point: float
    path_depth: float
    size: float
    own_commit: float
    curriculum: float = 0.0  # D13. 기본값이 있는 이유는 아래 주석 참고
    provenance: str = "unmeasured"


# D13 (2026-07-31): curriculum에만 dataclass 기본값이 있는 건 스타일 불일치가 아니라
# 계약이다 -- 이 필드는 나중에 추가됐고, Weights(fan_in=..., own_commit=...)만 넘기는
# 기존 호출부(테스트 포함)가 "이 신호를 안 쓴다"는 뜻으로 계속 유효해야 한다.
# 0.0은 "측정 전 임의값"이 아니라 "이 호출자는 교안 신호를 요청하지 않았다"이며,
# 실제 운영 가중치는 weights/codemap_weights.json 하나에서만 온다(weights.py).


@dataclass(frozen=True)
class CurriculumMatch:
    """ 교안/요구사항 <-> 코드 토큰 겹침의 결정론적 계산 결과 (curriculum.py D13)

    matches에 없는 경로 = 겹침 0건. item_count는 매칭 시도된 teach+requirement
    건수라 "몇 건 중 몇 건이 걸렸나"를 파일 단위로 재구성할 수 있다.
    dropped_* 는 왜 어떤 단어가 무시됐는지를 설명하는 진단값이다(D14).
    """

    matches: Mapping[str, int]  # path -> 이 파일이 걸린 teach/requirement 건수
    item_count: int
    matched_terms: tuple[str, ...]
    dropped_generic: tuple[str, ...]  # 이 저장소에서 너무 흔해 변별력이 없던 토큰
    dropped_short: tuple[str, ...]  # 길이 게이트에 걸린 토큰


@dataclass(frozen=True)
class RankedFile:
    """ Tier 1 랭커의 출력 한 줄. rank_evidence는 이 항목 하나만으로 "왜 이 순위인지"
    설명 가능해야 한다(다른 항목과 대조하지 않고도) -- judgment/importance_rank.py의
    선례를 그대로 따른다. """

    path: str
    rank: int
    rank_score: float
    signals: Mapping[str, float]  # 정규화된 0..1 신호값 (fan_in, entry_point, ...)
    rank_evidence: Mapping[str, object]  # {"weights": ..., "terms": ..., "tie_break_depth": n}


@dataclass(frozen=True)
class CrewClaim:
    """ Tier 2(크루)가 주장하는 재랭킹 한 건. ground.py를 거치기 전의 원시값 --
    closed-vocabulary 검증 전이므로 path/role이 실제로 유효한지 이 시점엔 모른다. """

    path: str
    role: str
    delta_rank: int
    reason_code: str


@dataclass(frozen=True)
class CodeMapEntry:
    """ 최종 산출물 한 줄. Tier 2가 꺼졌거나 전부 거부됐으면 tier1_rank == rank,
    role/reason_code는 None이다. """

    path: str
    rank: int
    tier1_rank: int
    tier1_score: float
    role: str | None
    selected: bool
    reason_code: str | None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class CodeMap:
    extractor_version: str
    entries: tuple[CodeMapEntry, ...]
    tier2_applied: bool
    tier2_rejected: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectLimits:
    max_file_bytes: int = 300_000
    max_total_files: int = 4_000


@dataclass(frozen=True)
class CodeMapConfig:
    max_shortlist_files: int = 40
    max_shortlist_chars: int = 12_000
    # D13(2026-07-31, 저장소 소유자 결정): teaches/requirements가 랭킹에 반영되려면
    # Tier 2가 실제로 돌아야 한다 -- engine.py::CodeMapAnalysisEngine.__init__의
    # 기본값과 반드시 같이 바꾼다(OPEN_QUESTIONS.md D13). WHY/COST/EXIT는 그쪽에 기록.
    tier2_enabled: bool = True
    model_code: str | None = None
    max_rank_shift: int = 5


AttributionMap = Mapping[str, AttributionSignal]


@dataclass(frozen=True)
class StructureArea:
    """ 분석 문서의 '구조' 항목 한 줄. p05-3 LLM 응답의 structure[] 원소 그대로 """

    area: str
    files: tuple[str, ...]
    role: str


@dataclass(frozen=True)
class DecisionPoint:
    """ 분석 문서가 짚은 '판단이 개입된 지점' 하나. ground.py 검증(analysis_doc.py의
    parse_analysis_doc_response)을 통과한 것만 이 타입이 된다 -- file은 이미
    allowed_paths 안에 있고, related_teach는 이미 후보 teach id이거나 None이다. """

    title: str
    file: str
    symbol: str
    why_it_matters: str
    related_teach: str | None


@dataclass(frozen=True)
class AnalysisDoc:
    """ p05-3(코드 분석 문서) 스테이지의 검증된 산출물 """

    overview: str
    structure: tuple[StructureArea, ...]
    decision_points: tuple[DecisionPoint, ...]
    risks: tuple[str, ...]
