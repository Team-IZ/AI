""" 엔진 선택 팩토리 

설정(engine_mode)을 보고 어떤 엔진 구현체 줄지 결정
FastAPI 의존성 사용 -> 라우터는 get_analysis_engine을 보고 Depends로 받기만 함
내부에서 무엇이 반환되는지 모름
"""

from app.config import get_settings
from app.engines.base import AnalysisEngine
from app.engines.stub import StubAnalysisEngine

def get_analysis_engine() -> AnalysisEngine:
    mode = get_settings().engine_mode

    # 스텁 모드면 스텁으로
    if mode == "stub":
        return StubAnalysisEngine()

    # 실물은 vendor 규칙부와 NVIDIA 키를 끌고 오므로 필요할 때만 import한다.
    # 최상단에 두면 stub으로 띄울 때도(테스트·로컬) 그것들이 따라 올라온다.
    from app.engines.analysis.engine import RealAnalysisEngine

    return RealAnalysisEngine()