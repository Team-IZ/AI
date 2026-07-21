"""애플리케이션 설정 (.env 기반).

PLAN §1.5: APP_MODE=standalone | integrated 로 운영 모드를 전환한다.
엔드포인트·스키마는 두 모드에서 동일하며, 달라지는 것은 storage adapter 주입뿐이다.
"""
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 명세 §2: FastAPI 측 base path. Phase 2~4 라우터가 이 prefix 아래 붙는다.
# (빈 라우터 파일은 만들지 않는다 — 실제 엔드포인트 구현 시 생성.)
API_V1_PREFIX = "/api/v1"

# --- 목업 전용 개발 키 (B1 후속) --------------------------------------------
# standalone 목업 페이지가 `X-Internal-Key`로 보내는 **공개 상수**다. 비밀이 아니며
# 소스·문서에 그대로 노출돼도 무방하다 — `app_mode == "standalone"`일 때만 통하고
# integrated에서는 아예 조회되지 않기 때문이다(app/api/deps.py 참고).
#
# 환경변수로 덮어쓸 수 있게 만들지 않았다. 목업 페이지가 이 값을 하드코딩으로
# 갖고 있어서(사용자에게 키를 입력시키지 않는다는 요구) 설정으로 흔들리면 목업이
# 조용히 깨진다. 두 곳이 어긋나지 않는다는 것은 테스트로 고정한다
# (tests/test_internal_auth.py::test_mockup_page_uses_the_dev_key_constant).
STANDALONE_DEV_API_KEY = "iz-get-standalone-dev-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 운영 모드 (PLAN §1.5)
    app_mode: Literal["standalone", "integrated"] = "integrated"

    # --- B1(확정): Spring→FastAPI 서비스 간 인증 = 공유 API 키 ---
    # **실제 통합용 키.** Spring이 매 요청 헤더 `X-Internal-Key`에 실어 보내는
    # 비밀 문자열이며, 값은 아직 미정이라 기본은 빈 값이다.
    # 빈 값이면 검증 비활성(현재 동작 유지) — 설정되면 integrated에서 강제된다.
    # 목업이 쓰는 개발 키는 이것과 완전히 별개다(위 STANDALONE_DEV_API_KEY).
    internal_api_key: str = ""

    # NVIDIA LLM (Phase 3+에서 사용; Phase 1에서는 미사용)
    nvidia_api_key: str = ""

    # --- B6(확정) 운영 파라미터 ---
    # FastAPI가 스스로 지키는 값만 둔다. job 폴링 주기(3초)·최대 대기(15분)는
    # 명세 §2상 "재시도 횟수·간격의 주도권은 호출자 Spring"이므로 여기 두지 않는다.
    llm_timeout_sec: int = 600        # LLM 1회 호출 내부 타임아웃 (원본 파이프라인 값 계승)
    answer_timeout_sec: int = 120     # 동기 답변 제출(§4.2) 처리 예산
    callback_retry_max: int = 3       # B3 콜백 전송 실패 시 재시도 횟수 (지수 백오프)

    # --- §3.3(확정): 코드 원문 무저장 — FastAPI 임시 작업공간에서만 유지 후 삭제 ---
    # 기본값은 OS 임시 디렉터리 하위. 대용량 ZIP 대비로 별도 볼륨 지정 가능.
    workspace_dir: Path = Path(tempfile.gettempdir()) / "bigproject-ai-workspace"
    # TTL 수치 자체는 명세에 확정값이 없다(§3.3은 "attempt 종료 또는 TTL"까지만 규정).
    # 아래는 세션 시간 상한(time_limit_sec 예시 2400s)에 여유를 둔 잠정값 — B8 확정 대기.
    workspace_ttl_sec: int = 86400

    # Supabase (standalone 모드 전용; Phase 5에서 supabase_store가 사용)
    supabase_url: str = ""
    supabase_key: str = ""

    # CORS 허용 오리진 (쉼표 구분) — standalone 모드에서만 사용한다.
    # 명세 §1: integrated 모드의 호출자는 Spring뿐이고 React는 FastAPI를 직접
    # 호출하지 않으므로, 브라우저 preflight 대상이 아니다.
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
