""" attribution 스테이지 -> codemap 랭커로 넘기는 신호의 공용 타입

D11 (2026-07-30): code-importance-map 브랜치는 attribution 브랜치
(feature/own-commit-attribution)를 하드 import하지 않는다 -- 두 브랜치는
독립적으로 머지될 수 있어야 하고, attribution 브랜치가 아직 없어도 이 브랜치는
그대로 동작해야 한다(선택적 주입, 코드 의존 아님). 이 파일은 attribution의
compute_attribution() 결과를 codemap의 rank.py가 이해하는 값 객체로 바꾸는
어댑터 하나만 담는다 -- attribution 패키지 자체는 import하지 않고,
compute_attribution()이 반환하는 dict의 "모양"(app/engines/attribution/__init__.py의
file_attribution 행 키)만 알고 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AttributionSignal:
    """ 파일 하나에 대한 OWN_COMMIT 귀속 신호. rank.py가 own_commit 항을 계산할 때 쓴다 """

    path: str
    attribution_type: str  # "AUTHORED" | "MODIFIED" | "UNTOUCHED" | "UNKNOWN"
    changed_line_count: int
    confidence: float


def from_attribution_result(result: Mapping[str, Any]) -> dict[str, AttributionSignal]:
    """ compute_attribution()의 반환 dict -> {path: AttributionSignal}

    result["file_attribution"]의 각 행은 최소한
    {"path", "attribution_type", "changed_line_count", "confidence"}를 갖는다
    (app/engines/attribution/__init__.py:97-104, :66-73 두 분기 모두 공통).
    """
    signals: dict[str, AttributionSignal] = {}
    for row in result.get("file_attribution", []):
        signals[row["path"]] = AttributionSignal(
            path=row["path"],
            attribution_type=row["attribution_type"],
            changed_line_count=row["changed_line_count"],
            confidence=row["confidence"],
        )
    return signals
