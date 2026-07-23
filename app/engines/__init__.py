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
    
    # 실제 기능인데 아직 구현이 없어서 일단 실패 경고
    raise NotImplementedError(
        f"engine_mode={mode!r}의 엔진이 아직 없습니다. 이식 필요"
    )