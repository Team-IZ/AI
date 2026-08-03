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

D2(2026-08-03, 엣지케이스 수정): D1의 "멈춘다" 규칙에 구멍이 하나 있었다 -- 1순위
파일 하나가 max_chars 자체보다 크면(used=0인 첫 반복에서 이미 초과), 그 즉시
stopped=True가 되고 이후 랭크 전부가 truncated로 밀려 selected가 **완전히 빈
튜플**이 됐다. 2, 3위가 훨씬 작아서 예산에 충분히 들어갔을 파일이어도 마찬가지였다
(테스트 test_huge_top_ranked_file_does_not_starve_the_rest가 이 결과를 "정상"으로
못박아두고 있었다 -- 실은 의도치 않은 전멸이었다).
  WHY: "예산이 누적으로 소진돼 뒤가 밀리는 것"과 "이 파일은 애초에 절대 못 들어갈
  크기라 원천적으로 제외되는 것"은 다른 이유다. 후자만 골라서 skip(멈추지 않고
  다음 랭크로 계속)해도, D1이 막으려던 "랭크 40은 들어가고 5는 빠지는 뒤죽박죽"과는
  다르다 -- 5위가 빠진 이유가 "예산이 그 지점에서 소진돼서"가 아니라 "혼자서도
  전체 예산을 넘는 크기라서"로 항상 명확하고, 그보다 낮은 랭크가 새치기하는 것도
  아니기 때문이다. P04(feat/poc_full)의 app/code-fragment.js::buildCodeBlock()도
  이미 이 방식(continue, break 아님)이었다 -- 그쪽을 그대로 참고했다.
  COST: 여전히 "1위 파일이 화면에 안 보인다"는 사실 자체는 남는다 -- 완전 침묵
  (analysis_document 전체가 빔)보다는 낫다는 판단이다.
  EXIT: 이 절충이 마음에 안 들면(1위 파일이 잘려서라도 부분 포함되길 원하면)
  text_len > max_chars 분기를 지우고 그 파일의 앞부분만 잘라 넣는 방식으로 바꾼다 --
  다만 그러면 "code_block에 파일 하나가 통째로 들어있다"는 나머지 파이프라인의
  전제(analysis_doc.py 등)가 깨지므로 별도 검토 필요.
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

        if len(selected) >= max_files:
            stopped = True
            truncated.append(rf.path)
            continue

        if text_len > max_chars:
            # D2: 파일 자체가 전체 예산보다 큼 -- 이 파일만 건너뛰고(멈추지 않고)
            # 다음 랭크로 계속 진행한다. stopped를 세우지 않는다.
            truncated.append(rf.path)
            continue

        if used + text_len > max_chars:
            stopped = True
            truncated.append(rf.path)
            continue

        selected.append(rf.path)
        used += text_len

    return tuple(selected), tuple(truncated)
