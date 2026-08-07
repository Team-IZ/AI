""" 분석 엔진의 인터페이스만 정의 

Protocol = '구조적 타이핑'. 어떤 클래스든 여기 적힌 메서드 시그너처만 맞으면
AnalysisEngine으로 취급하여 상속 import 필요 없음
-> 팀원 PoC 가져올 때, 그 코드가 우리 base를 import 하지 않아도 analyze() 하나만 맞으면 그대로 엔진에 사용 
"""

from typing import Any, Protocol

class AnalysisEngine(Protocol):
    def analyze(
        self, request: dict[str, Any], zip_bytes: bytes | None = None,
        *, prefetched_root: str | None = None,
        prefetched_git: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """ 분석 실행. input: dict, output: dict

        순수하게 파이썬 dict만 주고 받기
        - request: body.model_dump() 결과 (snake_case 키)
        - zip_bytes: ZIP 업로드 방식일 떄만, GITHUB_URL 이면 None
        - prefetched_root: analysisInput 경로(D2 재fetch, jobs._run_via_analysis_input)로
          이미 fetch된 스캔 루트. 있으면 엔진 자신의 materialize() 호출을 건너뛰고
          이 경로를 그대로 스캔한다 -- D2가 "검증했던 바로 그 코드"를 보장하는 지점이
          여기다(엔진이 따로 클론하면 브랜치가 그 사이 움직인 새 코드를 볼 수 있다).
        - prefetched_git: D-analysis-b1(2026-08-07) -- prefetched_root와 짝으로 온다.
          `{resolved_branch, head_commit, git_history, git_history_source,
          history_truncated}` 순수 dict 모양(FetchedInput의 해당 필드들). D2 경로는
          refetch_pinned()가 이미 이 데이터를 다 갖고 있어 엔진이 다시 계산할 필요가
          없다 -- None이면(직접 fetch 경로) 엔진이 자기 fetch() 결과에서 채운다.
        - 반환: AnalysisResult 스키마에 대응하는 snake_case dict
        """