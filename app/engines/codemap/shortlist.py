""" 랭킹 -> 숏리스트. 순수 함수, 예산(파일 수/문자 수) 안에서 상위부터 채운다.

D1(실측 버그): 원본 app/code-fragment.js::buildCodeBlock()은 파일명 알파벳순으로
욕심쟁이(greedy) 채우기를 해서, 알파벳상 먼저 오는 큰 파일 하나가 12,000자 예산을
거의 다 먹어치우고 나머지 77개 파일은 남은 부스러기 예산 안에 못 들어가 탈락했다
(실측: 78개 중 1개만 생존). 이 함수는 두 가지로 그 재발을 막는다:
  1) 알파벳이 아니라 rank.py가 매긴 중요도 순서로 채운다.
  2) 순서 안에서 예산을 넘기는 파일을 만나면 그 자리에서 멈춘다(그 파일보다 랭크가
     낮은 파일을 건너뛰고 그 다음 파일을 새치기시키지 않는다) -- "왜 랭크 40번
     파일은 들어갔는데 랭크 5번은 빠졌는지" 같은 뒤죽박죽 결과가 나오지 않게 한다.
     대신 그 지점부터는 전부 truncated_paths로 기록해 예산 밖에 있음을 드러낸다.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from app.engines.codemap.models import RankedFile, RepoFile


def select_shortlist(
    ranked: Sequence[RankedFile],
    files: Mapping[str, RepoFile],
    *,
    max_files: int,
    max_chars: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """ (선택된 path 튜플, 예산 밖으로 밀려난 path 튜플) -- 둘 다 랭크 순서 유지 """
    selected: list[str] = []
    truncated: list[str] = []
    used = 0

    stopped = False
    for rf in ranked:
        if stopped:
            truncated.append(rf.path)
            continue

        f = files.get(rf.path)
        text_len = len(f.text) if f is not None else 0

        if len(selected) >= max_files or used + text_len > max_chars:
            stopped = True
            truncated.append(rf.path)
            continue

        selected.append(rf.path)
        used += text_len

    return tuple(selected), tuple(truncated)
