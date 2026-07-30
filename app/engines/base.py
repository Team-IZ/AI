""" 분석 엔진의 인터페이스만 정의 

Protocol = '구조적 타이핑'. 어떤 클래스든 여기 적힌 메서드 시그너처만 맞으면
AnalysisEngine으로 취급하여 상속 import 필요 없음
-> 팀원 PoC 가져올 때, 그 코드가 우리 base를 import 하지 않아도 analyze() 하나만 맞으면 그대로 엔진에 사용 
"""

from typing import Any, Protocol

class AnalysisEngine(Protocol):
    def analyze(
        self, request: dict[str, Any], zip_bytes: bytes | None = None
    ) -> dict[str, Any]:
        """ 분석 실행. input: dict, output: dict

        순수하게 파이썬 dict만 주고 받기
        - request: body.model_dump() 결과 (snake_case 키)
        - zip_bytes: ZIP 업로드 방식일 떄만, GITHUB_URL 이면 None
        - 반환: AnalysisResult 스키마에 대응하는 snake_case dict, 단 하나 예외로
          최상위 "ai_usage" 키(app.schemas.usage.AiUsage 모양의 dict 리스트,
          app.engines.shared.timing.LlmCallTimer로 만듦)를 같이 넣을 수 있다 --
          AnalysisResult에는 그런 필드가 없으므로(job 전체가 아니라 job 하나에
          여러 LLM 호출이 있을 수 있어서), jobs.py::run_analysis()가 이 키만
          따로 꺼내 AnalysisJobStatus.ai_usage에 옮기고 나머지로 AnalysisResult를
          만든다. D-timing(2026-07-30) 참고.
        """