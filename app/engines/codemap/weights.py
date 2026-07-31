""" 랭킹 가중치 파싱 -- 순수 함수만. 파일 읽기는 이 모듈이 하지 않는다.

judgment/rank_weights/rank_weights.json(origin/feat/poc_full)과 같은 패턴:
로직(가중합 계산, rank.py)과 수치(이 JSON)를 분리해서 재보정이 파일 교체 하나로
끝나게 한다 -- git revert가 그대로 롤백 경로가 된다.

D2 순수성 참고: 이 모듈 자체는 디스크를 읽지 않는다 -- parse_weights()는 이미
읽어들인 JSON 텍스트(문자열)를 받아 Weights로 바꾸기만 한다(json.loads만 사용,
os/pathlib import 없음). 실제 파일 읽기(open())는 조립부(app/engines/codemap/
__init__.py, composition root)의 몫이다 -- 순수 모듈 5개(graph/rank/shortlist/
ground/weights) 중 어느 것도 파일시스템을 직접 건드리지 않는다는 D2 보장을
이렇게 지킨다.
"""
from __future__ import annotations

import json

from app.engines.codemap.models import Weights

DEFAULT_WEIGHTS = Weights(
    fan_in=1.0, entry_point=1.0, path_depth=1.0, size=1.0, own_commit=1.0,
    # D13: curriculum만 0.0 -- 나머지 다섯의 1.0은 "측정 전 잠정 동일값"이지만 이건
    # 실측 결과에 따른 명시적 OFF다(codemap_weights.json의 provenance에 근거 전문).
    # 파일을 못 읽는 상황이라고 검증 안 된 신호가 켜지면 안 된다(폴백은 항상 보수적으로).
    curriculum=0.0,
    provenance="module-constant-fallback (codemap_weights.json 읽기 실패시)",
)


def parse_weights(json_text: str | None) -> Weights:
    """ codemap_weights.json 텍스트 -> Weights. 텍스트가 없거나 파싱 실패하면 기본값 """
    if not json_text:
        return DEFAULT_WEIGHTS
    try:
        data = json.loads(json_text)
    except ValueError:
        return DEFAULT_WEIGHTS

    w = data.get("weights", {})
    return Weights(
        fan_in=w.get("fan_in", DEFAULT_WEIGHTS.fan_in),
        entry_point=w.get("entry_point", DEFAULT_WEIGHTS.entry_point),
        path_depth=w.get("path_depth", DEFAULT_WEIGHTS.path_depth),
        size=w.get("size", DEFAULT_WEIGHTS.size),
        own_commit=w.get("own_commit", DEFAULT_WEIGHTS.own_commit),
        curriculum=w.get("curriculum", DEFAULT_WEIGHTS.curriculum),
        provenance=data.get("provenance", "unspecified"),
    )
