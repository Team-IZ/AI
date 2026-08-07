""" 애플리케이션 설정. 값은 .env 또는 환경변수에서 읽음 """

# lru_cache - 같은 인자로 다시 호출되면 이전 결과 그대로 돌려줌
# pydantic-settings: 외부 패키지(requirements.txt). pydantic의 설정 전용 확장.
#   BaseSettings — 클래스 필드를 환경변수와 자동으로 연결해주는 기반 클래스.
#   SettingsConfigDict — 그 동작을 세부 조정하는 설정 딕셔너리.
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

# 업무 API 공통 경로 접두사
# 개발단계 시 0, 계약 안정시 v1
API_V0_PREFIX = "/api/v0"

# 면담 브리프 전용. 명세서(`IZ-Get_면담브리프_생성API_명세서_v08.md`) §4가 이 경로를
# 그대로 지정한다 -- 다른 4개 엔드포인트와 버전 축이 분리된 별도 계약이라 접두사도 다르다.
API_INTERNAL_V1_PREFIX = "/internal/v1"

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

    # 용도별 기본 모델. 값은 provider 식별자다(벤더 접두어 포함) — 요청에
    # providerModelCode가 오면 그쪽이 이긴다(모델 선택은 operator 권한).
    # 팀원 실측 기준값이고 언제든 바뀐다 — 그래서 코드가 아니라 설정이다.
    #   분석  nemotron-3-ultra-550b  271초  (2026-08-03 실측, 문제 3개 기준)
    #   문답  minimax-m3             (D-model1, 2026-08-07 교체 -- 아래 단일호출
    #                                 지연분포 재실측 전까지 SESSION_TIMEOUT_S는
    #                                 옛 deepseek-v4-flash 기준 20초 그대로 둔다)
    #   교안  minimax-m3             25분   (강사가 수업 전까지만 끝나면 되므로 허용)
    #
    # D-model1(2026-08-07): deepseek-ai/deepseek-v4-flash deprecate 예정 통보로
    # 채점 기본 모델을 minimaxai/minimax-m3로 교체한다.
    #   WHY: 20개 후보 × 3역할(세션채점/보고서/면담브리프) × 3반복 실측 + Claude
    #        Sonnet 동일 파이프라인 응답을 품질 기준점으로 삼은 재채점 결과,
    #        minimax-m3가 채점(90%)·보고서(93%) 조합에서 종합 1위(76.7%)였다.
    #        (benchmarks/deepseek_v4_flash_replacement.py, 이 브랜치에서 직접 실행)
    #   COST: D116 4축 벤치마크 기준 minimax-m3는 소요시간이 느린 편(채점 31.4s) --
    #         속도보다 품질을 우선한 결정.
    #   EXIT: 다음 deprecate 통보가 오면 같은 하니스로 재실행(후보 셔틀리스트는
    #         이 결정 당시 살아있던 모델 기준이라 갱신 필요).
    # 🔴 채점 모델을 아무거나 바꾸지 마라. 2026-08-03에 12종을 같은 채점 프롬프트로
    # 실측했는데 **루브릭을 적용해 JSON까지 내는 모델이 사실상 deepseek-v4-flash
    # 하나였다**(위 D-model1로 교체됐지만 아래는 그때 탈락한 유형은 여전히 유효):
    #   · 추론형(nemotron-3-super/nano, gpt-oss)  사고 과정을 본문에 뱉거나
    #     max_tokens 1200을 사고가 먼저 써서 JSON이 잘린다
    #   · 소형(llama-3.1-8b)  1.3초로 제일 빠른데 우수·보통·애매를 전부 2점으로 준다.
    #     통과선이 3점이라 아무도 통과 못 하는 채점기가 된다 — 속도로 고르면 안 된다
    #   · 대형(llama-3.3-70b, glm-5.2, mistral-medium-3.5)  무료 티어에서 30초 무응답
    # 이전 값 mistralai/mistral-medium-3.5-128b는 **최소 프롬프트도 응답하지 않는다.**
    model_code_analysis: str = "nvidia/nemotron-3-ultra-550b-a55b"
    model_code_session: str = "minimaxai/minimax-m3"
    model_code_curriculum: str = "minimaxai/minimax-m3"
    # 면담 브리프: 요청에 providerModelCode 필드 자체가 없다(명세 §4.1 -- 다른 4개
    # 엔드포인트와 달리 operator가 모델을 못 고른다).
    #
    # D-model1 연장: 채점과 "같은 모델 재사용"이던 옛 방침을 버리고 역할별로 따로
    # 뽑는다. WHY: 위 벤치마크에서 minimax-m3는 이 역할(면담브리프, ib-1)에서만
    # 구조검사 통과율 67%(20케이스 중 3회 중 1회 타임아웃)로 약했다 -- 프롬프트가
    # 7블록으로 세 역할 중 가장 크고 무겁다. 같은 벤치마크에서 openai/gpt-oss-120b는
    # 이 역할 품질 73%(구조검사 100%)로 상위권이면서 3역할 다 가장 빨랐다(9.4s).
    # COST: 세션채점·면담브리프가 이제 서로 다른 모델이라 두 값을 따로 관리해야
    # 한다(예전엔 하나 바꾸면 둘 다 바뀌었다). EXIT: 위 D-model1과 동일.
    model_code_interview_brief: str = "openai/gpt-oss-120b"

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# NVIDIA 키는 개수가 가변이라(NVIDIA_API_KEY_1..N) Settings 필드로 못 만든다.
_KEY_PREFIX = "NVIDIA_API_KEY_"


@lru_cache
def load_api_keys_into_env() -> int:
    """`.env`의 `NVIDIA_API_KEY_<N>`을 실제 환경변수로 올린다. 올린 개수를 돌려준다.

    **왜 필요한가**: `pydantic-settings`는 `.env`를 읽어 `Settings` 필드를 채울 뿐
    `os.environ`을 건드리지 않는다. 그런데 vendor의 `NvidiaKeyPool.from_env()`는
    `os.environ`만 본다 — 그래서 **로컬에서 `.env`에 키를 넣어도 못 찾는다.**
    AWS는 진짜 환경변수라 안 터지지만, 로컬 실행이 우리 운영 계획의 절반이라
    여기서 메운다(PLAN §T9e).

    **이미 있는 환경변수는 덮지 않는다.** 배포 환경의 값이 저장소의 `.env`보다
    우선해야 한다 — 반대로 하면 운영 키가 로컬 파일에 밀린다.
    """
    if not ENV_FILE.exists():
        return 0

    loaded = 0
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith(_KEY_PREFIX) or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        # 값은 절대 로그에 남기지 않는다. 개수만 센다.
        if value and not os.environ.get(name):
            os.environ[name] = value
            loaded += 1
    return loaded


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