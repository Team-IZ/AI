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
    #   문답  deepseek-v4-flash      7~12초 (2026-08-03 실측)
    #   교안  minimax-m3             25분   (강사가 수업 전까지만 끝나면 되므로 허용)
    #
    # 🔴 채점 모델을 아무거나 바꾸지 마라. 2026-08-03에 12종을 같은 채점 프롬프트로
    # 실측했는데 **루브릭을 적용해 JSON까지 내는 모델이 사실상 이것 하나였다.**
    #   · 추론형(nemotron-3-super/nano, gpt-oss)  사고 과정을 본문에 뱉거나
    #     max_tokens 1200을 사고가 먼저 써서 JSON이 잘린다
    #   · 소형(llama-3.1-8b)  1.3초로 제일 빠른데 우수·보통·애매를 전부 2점으로 준다.
    #     통과선이 3점이라 아무도 통과 못 하는 채점기가 된다 — 속도로 고르면 안 된다
    #   · 대형(llama-3.3-70b, glm-5.2, mistral-medium-3.5)  무료 티어에서 30초 무응답
    # 이전 값 mistralai/mistral-medium-3.5-128b는 **최소 프롬프트도 응답하지 않는다.**
    model_code_analysis: str = "nvidia/nemotron-3-ultra-550b-a55b"
    model_code_session: str = "deepseek-ai/deepseek-v4-flash"
    model_code_curriculum: str = "minimaxai/minimax-m3"

    # analysis-inputs 분리(D1/D2/D3, 2026-08-06) 관련 설정.
    #
    # 🔴 기존 GIT_CLONE_TIMEOUT_S(materialize.py, 300초)는 그대로 둔다 -- 그건 비동기
    # /analyses 잡 경로용이고, 이건 백엔드 동기 호출(목표 p95 5초, 타임아웃 15초)용이라
    # 훨씬 짧아야 한다.
    analysis_input_clone_timeout_s: int = 10
    # Phase B(히스토리 수집)는 별도의 더 짧은 예산 -- 넘겨도 Phase A(코드 자체) 결과는
    # 절대 안 버린다. 커밋 개수가 아니라 시간으로 상한을 둔다는 D1 결정 그대로.
    git_history_budget_s: int = 3
    git_history_since_days: int = 180
    git_history_max_commits: int = 500
    # 콤마 구분 문자열(리스트 필드는 .env 파싱이 번거로워 pydantic-settings 관례상 문자열로 둠).
    allowed_repo_hosts: str = "github.com,www.github.com"
    # 비워두면(기본) presigned URL 다운로드를 전부 거부한다 -- SSRF 방지를 위한
    # fail-closed 기본값. 백엔드가 실제 스토리지 호스트를 알려주면 그때 채운다.
    allowed_storage_hosts: str = ""
    # ZIP에 .git이 없고 백엔드도 히스토리를 안 실어 보내면 기본은 200+빈 배열(D3).
    # true면 422 GIT_LOG_MISSING으로 전환 -- 이건 코드가 아니라 정책 결정이라
    # 설정값으로 백엔드에 맡긴다.
    zip_require_git_log: bool = False
    # 같은 입력이면 같은 id(derived, 기본) vs 매번 새 id(random). 백엔드의
    # analysisInputId 컬럼이 팀 단위로 UNIQUE면 derived가 충돌할 수 있어 대비해 둔다.
    analysis_input_id_mode: Literal["derived", "random"] = "derived"

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