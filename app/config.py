""" 애플리케이션 설정. 값은 .env 또는 환경변수에서 읽음 """

# lru_cache - 같은 인자로 다시 호출되면 이전 결과 그대로 돌려줌
# pydantic-settings: 외부 패키지(requirements.txt). pydantic의 설정 전용 확장.
#   BaseSettings — 클래스 필드를 환경변수와 자동으로 연결해주는 기반 클래스.
#   SettingsConfigDict — 그 동작을 세부 조정하는 설정 딕셔너리.
from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

# 업무 API 공통 경로 접두사
# 개발단계 시 0, 계약 안정시 v1
API_V0_PREFIX = "/api/v0"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # 우선순위: 실제 환경변수 -> .env 파일 -> 아래 기본값
    # local - 키 비어 있으면 인증 건너뛰기
    # production - 키 비어 있으면 기동 거부
    app_env: Literal["local", "production"] = "local"
    
    # Spring이 X-Internal-Key 헤더에 실어 보내는 공유 비밀
    # 값은 .env에만 둠.
    internal_api_key: str = ""
    
    # 분석 엔진 선택. 기본은 가짜(stub). 실물은 나중에 이식 후 "real"로
    engine_mode: Literal["stub", "real"] = "stub"

@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    
    # 인증 꺼지면 운영 배포 X, 에러
    if settings.app_env == "production" and not settings.internal_api_key:
        raise RuntimeError(
            "production 환경에서 INTERNAL_API_KEY가 비어 있습니다. "
            "인증이 비활성화된 채로 뜨는 것을 막기 위해 기동을 거부합니다."
        )
    return settings