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

# 🔴 옛 API_INTERNAL_V1_PREFIX("/internal/v1", 면담 브리프 전용)는 삭제됐다
# (2026-08-07). 백엔드 제안서·회신이 전부 /api/v0 하나로 말하고 있어 축을 나눌
# 근거가 없다 -- 접두사가 둘이면 백엔드 클라이언트 설정도 둘이 된다.

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

    # D-fix (redteam audit H10, 2026-08-04): 무상태 세션 커서(Cursor.mac)에 서명할 비밀.
    # internal_api_key와 반드시 분리한다 — 그건 매 요청 헤더로 평문 전송되는 인증
    # 크리덴셜이라(deps.py) 노출 표면이 훨씬 넓다. 이 값은 절대 전송되지 않고 서버가
    # HMAC 계산에만 쓴다 — 같은 값을 재사용하면 헤더 유출 사고 하나가 곧바로 서명
    # 위조 키 유출이 된다. 비어 있으면(로컬 개발 등) mac 발급·검증을 건너뛴다 —
    # Spring이 이 필드를 새로 받아들이기 전까지의 점진적 도입(하위호환) 단계.
    session_cursor_hmac_secret: str = ""
    
    # 분석 엔진 선택. 기본은 가짜(stub). 실물은 나중에 이식 후 "real"로
    engine_mode: Literal["stub", "real"] = "stub"

    # 앱 로그 레벨. 기본 INFO다 -- 분석 1건이 5분 가까이 걸리는데 그동안 남는 게
    # access log(202/200)뿐이라 "어디까지 갔나"를 볼 수 없었다(2026-08-10).
    # INFO면 LLM 호출 1건당 한 줄(stages.call)과 job 시작·종료가 남는다.
    log_level: str = "INFO"

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
    # D3(2026-08-25): nemotron이 NVIDIA 무료 티어 40RPM 상한으로 재시도를 소진하면
    #   이 모델로 폴백한다(stages.call의 fallback_model_code, _FALLBACK_TRIGGER_FAILURES).
    #   WHY: nemotron을 GMI Cloud로도 옮기려 했으나 GMI 쪽 nemotron-3-ultra는
    #        유료 전용이고 계정에 충전된 크레딧이 없어(`Insufficient balance`,
    #        API로 잔액 조회 불가 — 사람이 console.gmicloud.ai에서 결제해야 해소)
    #        당장은 막혀 있다. minimax-m3는 이미 GMI 경유로 안정 확인됐으므로
    #        (2026-08-25 GMI 이관 이후 503/RATE_LIMITED 0건 지속) 임시 우회로 재사용한다.
    #   COST: 코드 분석 품질이 nemotron 대비 minimax-m3 기준으로 낮아질 수 있다 —
    #        전량이 아니라 nemotron 소진 시에만 개입되므로 영향은 부분적이다.
    #   EXIT: GMI 계정에 결제수단이 등록되면 nemotron을 GMI_ROUTED_MODELS에 추가해
    #        1차 경로 자체의 RPM 상한을 없애고, 이 필드는 순수 안전망으로만 남긴다.
    model_code_analysis_fallback: str = "minimaxai/minimax-m3"
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

    # 코드 fetch(app/engines/analysis/fetch.py) 설정.
    #
    # ⚠️ 이름의 `analysis_input`은 폐기된 `/analysis-inputs` 분리 API의 흔적이다.
    # 키를 바꾸면 배포된 .env(팀원 App Runner)가 조용히 기본값으로 떨어지므로 그대로 둔다.
    #
    # D-clone-timeout(2026-08-21): 10초는 실제로 너무 짧았다. 운영 DB 직접 대조
    # (`analysis_job.failure_reason = '클론이 10초를 넘겼습니다'`)로 확인 -- 최근 7일
    # GITHUB_URL 분석 31건 중 5건(16%)이 이 정확한 사유로 실패했고, 그중 4건이 같은 날
    # 90분 안에 몰렸다. LLM은 한 번도 안 불렸으니(aiUsage: []) 학생 코드 문제가 아니라
    # 순수히 타임아웃이 타이트했던 것이다.
    #   WHY: `materialize.py`의 GIT_CLONE_TIMEOUT_S(같은 종류의 학생 레포 `--depth 1`
    #        클론, "학생 레포는 작지만 상한이 없으면 job 하나가 워커를 무한정 잡는다"는
    #        같은 근거)가 이미 300초로 서 있다. 이 값이 10초로 훨씬 짧아야 할 이유는
    #        주석에도, 커밋 이력에도 없다 -- 의도된 설계 차이가 아니라 그냥 어긋난
    #        값으로 보여서, 같은 작업을 이미 검증된 값에 맞춘다.
    #   COST: 진짜로 죽은(응답 없는) 호스트를 상대할 때 실패 판정까지 더 오래 걸린다.
    #        백엔드는 job을 비동기 폴링하므로(agentAsync 워커 블로킹 없음, 실측: 정상
    #        성공 job도 712초 걸림) 이 정도 지연은 이미 감내되는 범위다.
    #   EXIT: 이 값이 다시 문제가 되면(예: 진짜 응답 없는 호스트가 워커를 오래 잡는
    #        사례가 쌓이면) fetch.py 쪽만 별도로 낮추거나, git clone과 ZIP 다운로드
    #        (`_download()`)가 지금 이 설정 하나를 같이 쓰는 것부터 갈라야 한다.
    analysis_input_clone_timeout_s: int = 300

    # D4(2026-08-25): 동시에 실제로 돌아가는 무거운 job(코드분석+교안분석) 수의
    #   전역 상한 -- app/concurrency.py의 HEAVY_JOB_CONCURRENCY가 이 값으로
    #   만들어지고, jobs.py와 curricula.py가 세마포어 하나를 같이 쓴다.
    #   WHY: 실측 인시던트 -- KST 22:08~22:10에 코드분석 job 12개가 거의 동시에
    #        시작되자 단일 인스턴스(당시 1vCPU)가 22:11부터 CPU 100%로 고정,
    #        헬스체크조차 15초간 응답 0바이트(curl 재현), 5xx 발생. job 하나가
    #        내부에서 ThreadPoolExecutor(max_workers=8)를 두 번(questions.py
    #        질문 선정용, hints.py 힌트 생성용) 새로 띄우는데, job 개수에 대한
    #        전역 상한이 없어 12개 job이면 이론상 최대 96스레드가 동시에
    #        CPU를 다퉜다. 교안분석(curriculum.py)도 hints.MAX_PARALLEL을
    #        그대로 재사용해 같은 구조(job당 최대 8스레드)라, 두 job 타입이
    #        상한을 따로 들면 "코드분석 6개 + 교안분석 6개 동시"로 같은 사고가
    #        재현될 수 있어 하나의 세마포어를 공유시킨다. App Runner
    #        오토스케일링은 MaxConcurrency(동시 HTTP 요청 수) 기준이라 이 상황
    #        (요청 수는 12로 낮음)에서 전혀 발동하지 않는다는 것도 같은
    #        조사로 확인함(ActiveInstances가 인시던트 내내 1로 고정).
    #        리포트 생성(reports.py)은 job당 stages.call을 스레드풀 없이
    #        1회만 호출하는 순차 구조라 CPU 부담이 구조적으로 훨씬 작고,
    #        실측 근거 없이 상한부터 거는 게 과잉이라 판단해 이 상한에서
    #        의도적으로 뺐다(2026-08-25 조사).
    #   COST: 상한을 넘는 job은 큐(QUEUED)에서 대기 -- 처리량이 아니라
    #        지연으로 부하를 흡수한다(서버 다운 대신 대기시간 증가). 코드분석과
    #        교안분석이 같은 예산을 나눠 쓰므로, 둘이 동시에 몰리면 서로의
    #        대기시간을 늘린다(단, 어느 한쪽만 몰릴 때보다 CPU 포화로 서버
    #        전체가 죽는 것보다는 낫다는 게 이 결정의 전제).
    #        값 6은 정밀 계산이 아니라 "인시던트를 낸 값(12)의 절반, 인스턴스를
    #        2vCPU로 올린 뒤에도 보수적으로 시작"이라는 안전마진 판단이다 --
    #        실측 기반 최적값이 아니므로 재조정이 전제된 잠정치다.
    #   EXIT: 배포 후 CloudWatch CPUUtilization을 이 값과 함께 관찰해
    #        여유가 있으면 올리고, 다시 포화되면 낮춘다. reports.py도 실제로
    #        CPU 포화에 기여하는 게 확인되면 같은 세마포어에 합류시킨다.
    analysis_max_concurrent_jobs: int = 6
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