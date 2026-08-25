""" LLM 호출 래퍼. vendor/ 의 NVIDIA 클라이언트를 감싸고 ai_usage 원장을 만든다.

여기는 우리 소유고 vendor/ 는 상류(nvidia-build) 소유다(vendor/SOURCE.md).
이 파일이 더하는 것: 키 풀 싱글턴 · 지연 측정 · 토큰 추출 · 실패 코드 분류.

실패해도 원장은 남아야 한다 — 실패한 호출도 토큰을 태우고 비용이 든다.
그래서 예외에 usage를 붙여 던진다(LlmError.usage).
"""

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_VENDOR = Path(__file__).parent / "vendor"

# D1: minimax-m3만 GMI Cloud로 라우팅한다(2026-08-25).
#   WHY: NVIDIA 무료 티어는 (키,모델) 쌍당 40 RPM(vendor/SOURCE.md) 고정 상한이고,
#        키 8개를 풀링해도 320 RPM인데 minimax-m3(세션 채점 경로)의 실제 동시 트래픽이
#        이를 지속적으로 초과해 학생 답변 제출이 503으로 계속 실패했다(2026-08-25
#        인시던트). GMI Cloud는 동일 모델을 사용량 기반 과금(트래픽에 따라 확장)으로
#        제공하고 OpenAI 호환이라 응답 스키마가 NVIDIA와 같다 — 스모크 테스트로 확인
#        (8회 연속 즉시 200, response_format=json_object 정상).
#   COST: 프로바이더가 하나 더 늘어(GMI_API_KEY 관리 포인트 추가), 두 벤더의 에러
#        분류·재시도 의미가 실제로 같은지는 지금은 HTTP 상태코드 수준까지만 확인했다
#        (본문 형식이 달라지면 _classify가 오분류할 수 있음).
#   EXIT: nemotron 등 다른 모델도 같은 문제가 생기면 이 집합에 추가. NVIDIA 쪽
#        rate limit이 해소되면(포럼 증량 승인·유료 전환) 집합을 비워 되돌린다.
# D9(2026-08-25, D7 배포 직후): 코드분석 1차 모델을 minimax-m3에서
#   minimax-m2.7로 낮춘다.
#   WHY: D7로 minimax-m3(GMI)를 코드분석 1차로 옮긴 뒤에도 job이 fetch 완료
#        이후(코드로는 stages.call 지점으로 추정, CPU 사용률이 5~8%로 낮아
#        무한루프가 아니라 I/O 대기로 보임) 15분 넘게 정체하는 사례가 재현됨
#        (원인 미확정, 진단 로그를 stages.py/client.py에 추가하는 건 별도
#        PR로 진행). 이 job은 원래 nemotron-3-ultra-550b(대형)가 맡던 일이라
#        m3보다 가벼운 모델로 낮추면 그 정체 자체가 해소될 수 있다는 판단.
#        GET /v1/models로 확인한 실측 근거: m2.7은 m3의 정확히 절반 가격
#        ($0.3/$1.2 대 $0.6/$2.4, 백만 토큰당)이고 context window도 훨씬
#        작다(196,608 대 1,048,576) — 가격이 모델 크기의 대리 지표라는 점에서
#        "더 가벼운 모델"이라는 판단의 근거는 있지만, 실제 지연시간(latency)
#        차이는 아직 실측 전이다(간접 추정임을 명시).
#   COST: 코드 분석 품질이 m3 대비 더 낮아질 수 있다(D7이 nemotron 대비
#        낮아질 수 있다고 이미 인지한 트레이드오프 위에 한 단계 더). context
#        window가 192K로 줄어 매우 큰 레포(코드블록 예산을 넘는 경우)에서
#        더 쉽게 잘릴 수 있다 — 다만 stages.py의 truncation 예산(code_block
#        12,000자 등)이 이미 192K 토큰보다 훨씬 작아 실질 영향은 없을 것.
#   EXIT: 진단 로그(별도 PR)로 정체 원인이 m3 자체와 무관하다고 밝혀지면
#        다시 m3로 되돌린다. m2.7에서도 같은 정체가 재현되면 모델 크기가
#        원인이 아니라는 뜻이므로 다른 가설(GMI 커넥션 자체의 타임아웃 부재
#        등)로 넘어간다.
GMI_ROUTED_MODELS = {"minimaxai/minimax-m3", "minimaxai/minimax-m2.7", "openai/gpt-oss-120b"}
GMI_API_URL = "https://api.gmi-serving.com/v1/chat/completions"
# NVIDIA 카탈로그 표기(소문자, model_code — 원장·로그에 쓰는 우리 값)와 GMI 카탈로그
# 표기(대소문자 그대로, `GET /v1/models`로 확인함)가 다르다. model_code 자체는 안
# 바꾸고 GMI에 보낼 때만 변환한다.
#
# D11(2026-08-26): gpt-oss-120b는 GMI로 라우팅한다(활성). nemotron-3-ultra-550b는
#   GMI_MODEL_IDS 매핑만 넣고 GMI_ROUTED_MODELS엔 아직 넣지 않는다(대기).
#   WHY: GET /v1/models로 두 모델 다 GMI 카탈로그에 실존함을 확인했고(표기가
#        우리 model_code와 완전히 동일 — MiniMax와 달리 대소문자 변환 불필요),
#        gpt-oss-120b는 실제 chat completion 호출로 200 확인됨(2026-08-26).
#        nemotron-3-ultra-550b는 같은 방식으로 찔러보니 매번
#        {"code":"rate_limit_exceeded","message":"All endpoints are currently
#        overloaded"} — GMI 쪽 이 모델 전용 공급 부족이고 우리 요청 빈도와는
#        무관하다. nemotron은 지금 model_code_analysis_fallback으로 NVIDIA
#        경유가 이미 정상 동작 중이다(호출 빈도가 낮아 RPM에 잘 안 걸림) —
#        여기서 GMI_ROUTED_MODELS에 넣으면 지금 되는 fallback을 지금 안 되는
#        것으로 바꾸는 퇴행이 된다.
#   COST: nemotron의 GMI_MODEL_IDS 매핑은 지금은 죽은 코드다 — 아무도 안
#        쓰므로 GMI 쪽 표기가 바뀌어도 아무도 알아채지 못한다.
#   EXIT: GMI `/v1/models` 재조회 또는 실제 호출로 nemotron이 더 이상
#        rate_limit_exceeded를 안 낸다고 확인되면 GMI_ROUTED_MODELS에 추가한다.
GMI_MODEL_IDS = {
    "minimaxai/minimax-m3": "MiniMaxAI/MiniMax-M3",
    "minimaxai/minimax-m2.7": "MiniMaxAI/MiniMax-M2.7",
    "openai/gpt-oss-120b": "openai/gpt-oss-120b",
    "nvidia/nemotron-3-ultra-550b-a55b": "nvidia/nemotron-3-ultra-550b-a55b",
}

# 배치용 상한. 팀원 실측(shared/llm.js:143~147): step-3.7-flash는 reasoning_effort=low
# 로도 실제 프롬프트에서 39초가 걸렸고 재시도가 120초에서 통째로 타임아웃했다.
# 상류 vendor 기본값이 600초인 것도 그 때문이다 — 짧게 잡으면 성공할 호출을 죽인다.
# 분석 배치는 1시간 예산이라 이 값이 말이 된다.
DEFAULT_TIMEOUT_S = 600.0

# 세션용 상한. **학생이 화면 앞에서 기다리는 경로에는 배치 값을 쓰면 안 된다.**
#
# 🔴 2026-08-03 재측정으로 8초를 20초로 되돌렸다. 옛 8초는 mistral-medium-3.5의
# TTFT 분포(정상 draw 중앙값 0.62초)에서 나온 값인데, **그 모델이 죽었다** —
# "1+1은?" 같은 최소 프롬프트도 3/3 30초 타임아웃이다. 근거가 사라진 상한이다.
#
# 지금 채점 모델(deepseek-v4-flash)의 실측 분포는 단봉이고 훨씬 느리다.
#
#   성공  6.9 · 10.4 · 11.1 · 11.7초         ← 8초면 이 중 셋을 죽인다
#   실패  HTTP 529 Overloaded, 0.3초에 즉답   ← 기다림 자체가 없다
#
# 실패 모드가 "정체"에서 "즉시 529"로 바뀐 것이 핵심이다. 옛날에는 상한이 정체를
# 빨리 버리는 장치라 짧을수록 이득이었지만, 지금 실패는 공짜로 빨리 오므로 상한은
# **성공할 호출을 죽이지 않는 것**만 신경 쓰면 된다.
#
# ⚠️ D-model1(2026-08-07): model_code_session이 deepseek-v4-flash -> minimax-m3로,
# model_code_interview_brief가 -> openai/gpt-oss-120b로 바뀌었다(config.py 참고).
# 위 6.9~11.7초 분포는 deepseek-v4-flash 기준이라 이제 근거가 낡았다 -- 20초를
# 그대로 둔 건 데이터가 있어서가 아니라 **아직 두 새 모델의 단일 호출(재시도 제외)
# 지연분포를 재실측 안 해서**다(벤치마크의 mean_elapsed_s는 재시도 포함 합산값이라
# 이 상수의 근거로 못 쓴다 -- CLAUDE.md §13 Data-First Numerics).
SESSION_TIMEOUT_S = 20.0

# 세션 경로 재시도 횟수.
#
# 실패가 0.3초 즉답(529)이라 시행 자체는 싸다. 그래서 횟수를 늘리는 대신 줄였다 —
# 20초 × 6회면 최악 120초지만, 그 최악은 6회 연속 "느린 성공"일 때만 나오고
# 529 연발이면 백오프 포함 15초 안에 끝난다(stages.call이 시행 사이에 쉰다).
#
# ⚠️ 타임아웃과 함께 줄이지 마라. 예산(시간 × 횟수)이 실패율을 정한다.
SESSION_MAX_ATTEMPTS = 6

# reasoning_effort는 모델마다 지원 여부가 다르다. Mistral에 "low"를 보내면 무시가 아니라
# 하드 HTTP 400이다(팀원 실측). 그래서 전역 파라미터가 아니라 모델별 맵이어야 한다.
#
# step-3.7-flash에 "low"가 필요한 이유: 기본값(medium)이면 답 전체를 reasoning_content에만
# 쓰다가 content에 닿기 전에 max_tokens로 잘린다. "low"가 그 실패 모드를 없앤다.
# 지연 자체는 안 줄어든다 — 그건 위 타임아웃이 흡수한다.
#
# 🔴 **nemotron에는 넣지 마라**(2026-08-13 실측). 같은 p04-7 프롬프트에 "low"를 주면
# 잘림은 안 없어지는데 **힌트가 영어로 나온다**("Think about how the dictionary get
# method behaves..."). 사고 예산을 줄이면서 언어 지시까지 같이 흘리는 것으로 보인다 --
# 학생이 그대로 읽는 문장이라 잘린 것보다 나쁘다.
REASONING_EFFORT_BY_MODEL = {
    "stepfun-ai/step-3.7-flash": "low",
}

# 추론형 모델은 답과 사고를 같은 max_tokens 예산에서 쓴다. 매니페스트의 값은
# "답에 필요한 길이"라 그대로 주면 사고가 먼저 예산을 소진하고 답이 잘린다.
# 실측(p04-1, step-3.7-flash, 프롬프트 12,488자): 답 3,219자(~1,100토큰)인데
# 완료 토큰 5,840 — 사고가 약 4,700. 매니페스트 값 2,400으로는 두 번 다 잘렸다.
#
# 여기 없는 모델은 배수 1.0으로 시작하고, 잘리면 stages.call()이 예산을 두 배로
# 올려 재시도한다. 모델마다 실측하려면 nemotron은 콜 하나에 시간이 오래 걸린다.
REASONING_TOKEN_MULTIPLIER = {
    "stepfun-ai/step-3.7-flash": 3.0,
    # 분석 기본 모델도 추론형이다. 1.0으로 두면 p04-3(매니페스트 1600)이 사고에만
    # 예산을 다 쓰고 JSON을 시작도 못 한다 — 2026-08-03 실호출에서 두 시도 다
    # 사고 원문이 그대로 나와 "JSON을 찾을 수 없습니다"로 job이 통째로 FAILED 났다.
    # 재시도 배수 상승(finish_reason=length)은 잘린 뒤에야 도는 사후 장치라,
    # 매번 1콜을 버리고 시작하는 것과 같다.
    "nvidia/nemotron-3-ultra-550b-a55b": 3.0,
}


def budget_for(model_code: str, manifest_max_tokens: int | None) -> int | None:
    """매니페스트의 max_tokens를 그 모델이 실제로 필요한 예산으로 환산한다."""
    if manifest_max_tokens is None:
        return None
    return int(manifest_max_tokens * REASONING_TOKEN_MULTIPLIER.get(model_code, 1.0))


_pool = None
_pool_lock = threading.Lock()


class LlmError(RuntimeError):
    """LLM 호출 실패. 원장에 남길 usage를 함께 들고 있다.

    `status_code`는 공급자가 준 HTTP 상태다(HTTPError가 아니면 None).
    **원장에는 안 나간다** — `ai_usage`는 컬럼이 고정이라 늘릴 수 없고, 이 값은
    재시도할지 판단하는 내부용이다(`stages.call`).
    """

    def __init__(self, message: str, usage: dict[str, Any], status_code: int | None = None):
        super().__init__(message)
        self.usage = usage
        self.status_code = status_code


def is_retryable(status_code: int | None) -> bool:
    """다시 불러서 결과가 달라질 여지가 있나.

    🔴 2026-08-10: 그전엔 실패 코드만 보고 재시도해서 **404를 6번 던졌다**
    (백엔드가 providerModelCode에 Swagger 기본값 `"string"`을 실어 보낸 실측).
    모델이 없다는 답은 다음에도 똑같다 — 12초와 원장 6행을 버렸다.

    - None: 네트워크·타임아웃 등 HTTP 응답 자체가 없던 경우. 재시도 가치가 있다.
    - 5xx: 공급자 쪽 일시 장애(503 워커 포화 포함). 재시도한다.
    - 408·429: 4xx지만 "지금은 안 됨"이라 재시도한다.
    - 그 밖 4xx(400·401·403·404): 요청이 틀린 것이다. 고치기 전엔 몇 번을 불러도 같다.
    """
    if status_code is None:
        return True
    if status_code >= 500:
        return True
    return status_code in (408, 429)


@dataclass(frozen=True)
class LlmResult:
    content: str
    usage: dict[str, Any]   # AiUsage의 일부. featureCode·contextId 등은 호출자가 채운다
    raw: dict[str, Any]


def _load_vendor():
    """vendor를 sys.path에 꽂고 필요한 심볼을 돌려준다.

    nvidia_client.py가 `from nvidia_key_pool import ...` 로 플랫 import를 한다.
    원본을 안 고치기로 했으므로 경로를 맞추는 쪽이 우리다 — vendor/SOURCE.md 참조.
    """
    path = str(_VENDOR.resolve())
    if path not in sys.path:
        sys.path.insert(0, path)

    from nvidia_client import NvidiaRotatingClient  # type: ignore[import-not-found]
    from nvidia_key_pool import KeyPoolExhausted, NvidiaKeyPool  # type: ignore[import-not-found]

    return NvidiaRotatingClient, NvidiaKeyPool, KeyPoolExhausted


def _gmi_chat(model_code: str, messages: list[dict], *, timeout_s: float, **kwargs) -> dict:
    """POST /chat/completions to GMI Cloud. 단일 키라 NVIDIA처럼 풀링·로테이션이 없다
    (GMI_ROUTED_MODELS 주석 EXIT 참고 — 키가 여러 개 필요해지면 그때 분리한다).

    GMI는 OpenAI 호환이라 요청·응답 스키마가 NVIDIA와 같다 — `chat()`의 `_extract_content`/
    `_extract_tokens`/`_classify`를 그대로 재사용할 수 있는 이유다.

    D10 continued(2026-08-25): `urllib.request.urlopen(timeout=)`은 **개별 소켓
    연산(connect·recv) 사이의 최대 대기시간**이지 요청 전체의 총 소요시간 상한이
    아니다 — 서버가 연결은 유지한 채 청크 간격을 timeout_s보다 짧게만 유지하며
    아주 느리게 응답하면, 전체적으로는 timeout_s(기본 600초)를 몇 배 넘겨도
    소켓 타임아웃이 안 걸릴 수 있다. 2026-08-25 인시던트(job이 15분+ 정체,
    CPU는 낮아 블로킹 대기로 추정)의 유력한 원인 후보 — 아직 확정은 아니고
    이 로그로 재현 시 정확히 확인한다.
    """
    api_key = os.environ.get("GMI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GMI_API_KEY가 설정되지 않았습니다")

    gmi_model = GMI_MODEL_IDS.get(model_code, model_code)
    body = json.dumps({"model": gmi_model, "messages": messages, **kwargs}).encode()
    req = urllib.request.Request(
        GMI_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # GMI는 Cloudflare 뒤에 있다. urllib 기본 UA("Python-urllib/x.x")는
            # 봇으로 분류돼 403(error code 1010)으로 막힌다(2026-08-25 실측) --
            # curl은 통과하는데 urllib 기본값만 막혀서 UA 문제였음을 확정했다.
            "User-Agent": "curl/8.0",
        },
    )
    log.info("_gmi_chat: 요청 전송 model=%s timeout=%ss", gmi_model, timeout_s)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        log.info("_gmi_chat: 응답 헤더 수신 status=%s", resp.status)
        raw = resp.read()
        log.info("_gmi_chat: 응답 바디 수신 완료 %d bytes", len(raw))
        return json.loads(raw)


def get_pool():
    """키 풀 싱글턴. 풀이 예산을 기억하므로 호출마다 새로 만들면 안 된다.

    새로 만들면 각 인스턴스가 자기 슬라이딩 윈도우만 봐서 실제 한도를
    N배로 초과한다 — 429가 나기 시작한다.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                # vendor의 from_env()는 os.environ만 본다. .env에 있는 키를 먼저
                # 올려주지 않으면 로컬 실행에서 키를 못 찾는다(config 주석 참고).
                from app.config import load_api_keys_into_env

                load_api_keys_into_env()
                _, NvidiaKeyPool, _ = _load_vendor()
                _pool = NvidiaKeyPool.from_env()
    return _pool


def _extract_content(body: dict[str, Any]) -> tuple[str, str | None]:
    """응답에서 본문과 종료 사유를 뽑는다.

    이 계열 모델은 content(답)와 reasoning_content(사고 과정)를 둘 다 낸다.
    **reasoning_content를 답으로 대체하면 안 된다** — 출력이 잘리면 사고 과정만
    남는데, 그걸 답으로 돌려주면 JSON 스테이지에서 "모델이 JSON을 안 줬다"로
    오진된다. 실제로 max_tokens=16 실측에서 그 일이 났다.
    """
    choices = body.get("choices") or []
    if not choices:
        return "", None
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip(), choices[0].get("finish_reason")


def _extract_tokens(body: dict[str, Any]) -> dict[str, int]:
    usage = body.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "input_token_count": int(usage.get("prompt_tokens") or 0),
        "output_token_count": int(usage.get("completion_tokens") or 0),
        "cached_token_count": int(details.get("cached_tokens") or 0),
    }


def _classify(exc: Exception, detail: str) -> str:
    """예외를 ai_usage.failure_code 5종 중 하나로.

    INVALID_JSON은 여기서 안 난다 — 파싱은 스테이지 계층(T7b)의 일이다.
    """
    if isinstance(exc, TimeoutError) or "timed out" in detail.lower():
        return "TIMEOUT"
    # 429는 우리 키의 분당 한도, 529는 공급자 전체 과부하다. 원인은 다르지만
    # 둘 다 "지금은 안 되고 조금 뒤엔 될 수 있다"라서 원장에서 같은 칸에 넣는다.
    # 529를 PROVIDER_ERROR로 두면 진짜 장애와 섞여 실패 통계를 못 읽는다.
    #
    # 🔴 503 추가(2026-08-10). 진행 로그를 켜자마자 실패의 대부분이 이거였다:
    #   HTTP 503 {"message":"ResourceExhausted: Worker local total request limit
    #             reached (202/32)","type":"Service Unavailable","code":503}
    # NVIDIA 워커 큐가 찬 것이고 0.4초에 즉답한다 -- 529와 완전히 같은 성격인데
    # PROVIDER_ERROR로 들어가 **진짜 장애와 섞이고 있었다**(위 주석이 막으려던 그것).
    # 백엔드에도 영향이 있다: PROVIDER_ERROR는 analysis_job.failure_code를 MODEL_ERROR로
    # 만든다(HttpAnalysisServerClient.toFailureCode) -- 모델이 멀쩡한데 모델 탓이 된다.
    # 재시도 대상 집합은 그대로라(stages.call) 동작은 안 바뀌고 분류만 바로잡힌다.
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 503, 529):
        return "RATE_LIMITED"
    # 컨텍스트 초과는 400으로 오고 본문 문구로만 구분된다. 문구가 바뀌면
    # PROVIDER_ERROR로 떨어질 뿐이라 안전하게 실패한다.
    if "context length" in detail.lower() or "maximum context" in detail.lower():
        return "CONTEXT_OVERFLOW"
    return "PROVIDER_ERROR"


_NVAPI_KEY_RE = re.compile(r"nvapi-[A-Za-z0-9_-]+")
# GMI 키는 JWT라 nvapi- 패턴과 다르다. 같은 방어를 GMI 응답 본문에도 적용한다.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def _http_detail(exc: Exception) -> str:
    """HTTPError면 ` (HTTP 401 Unauthorized: {본문})`, 아니면 빈 문자열.

    🔴 2026-08-10: 이게 없어서 배포본 장애를 못 읽었다. 4회 호출이 전부 27~98ms 만에
    `PROVIDER_ERROR: HTTPError`로 죽었는데, 예외 종류만 남기고 상태코드를 버려서
    401(키 거부)인지 404(모델 없음)인지 402(크레딧 소진)인지 가릴 수 없었다.
    상태코드·사유구는 비밀이 아니다. 본문은 키를 되비추지 않지만 방어적으로
    `nvapi-*`를 지우고 200자로 자른다.
    """
    if not isinstance(exc, urllib.error.HTTPError):
        return ""
    try:
        body = exc.read().decode(errors="replace")[:200]
    except Exception:  # 본문을 이미 읽었거나 스트림이 닫힌 경우 -- 상태코드만으로도 충분하다
        body = ""
    body = _NVAPI_KEY_RE.sub("nvapi-[REDACTED]", body)
    body = _JWT_RE.sub("jwt-[REDACTED]", body).strip()
    head = f" (HTTP {exc.code} {exc.reason}"
    return f"{head}: {body})" if body else f"{head})"


def chat(model_code: str, messages: list[dict], *, timeout_s: float = DEFAULT_TIMEOUT_S,
         **kwargs) -> LlmResult:
    """LLM 한 번 호출. 성공하면 LlmResult, 실패하면 LlmError(usage 포함).

    CPU가 아니라 네트워크 대기지만, 동기 호출이라 이벤트 루프에서 직접 부르면 안 된다.
    지금은 run_analysis가 동기 함수(`def`)라 Starlette이 threadpool로 돌려준다.
    """
    _, _, KeyPoolExhausted = _load_vendor()
    effort = REASONING_EFFORT_BY_MODEL.get(model_code)
    if effort and "reasoning_effort" not in kwargs:
        kwargs = {**kwargs, "reasoning_effort": effort}

    base = {
        "model_code": model_code,
        "occurred_at": datetime.now(timezone.utc),
        "input_token_count": 0,
        "output_token_count": 0,
        "cached_token_count": 0,
    }
    started = time.monotonic()

    try:
        if model_code in GMI_ROUTED_MODELS:
            body = _gmi_chat(model_code, messages, timeout_s=timeout_s, **kwargs)
        else:
            NvidiaRotatingClient, _, _ = _load_vendor()
            client = NvidiaRotatingClient(pool=get_pool(), timeout_s=timeout_s)
            body = client.chat(model_code, messages, **kwargs)
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        # 키 풀 포화는 우리가 던진 것이라 별도로 분류한다(429와 같은 뜻).
        detail = "" if isinstance(exc, KeyPoolExhausted) else str(exc)
        failure_code = "RATE_LIMITED" if isinstance(exc, KeyPoolExhausted) else _classify(exc, detail)
        usage = {**base, "status": "FAILED", "failure_code": failure_code, "latency_ms": latency_ms}
        # 예외 문구에 키가 실릴 일은 없지만(vendor가 Authorization을 로그에 안 남긴다)
        # 그래도 원문을 그대로 전파하지 않고 종류만 남긴다.
        status_code = exc.code if isinstance(exc, urllib.error.HTTPError) else None
        raise LlmError(f"{failure_code}: {type(exc).__name__}{_http_detail(exc)}",
                       usage, status_code) from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    content, finish_reason = _extract_content(body)
    usage = {
        **base,
        **_extract_tokens(body),
        "status": "SUCCEEDED",
        "failure_code": None,
        "latency_ms": latency_ms,
    }

    if not content:
        # 답이 비었다. 대개 max_tokens를 사고 과정이 다 써버린 경우다.
        # 조용히 빈 문자열을 돌려주면 다음 단계가 "빈 JSON"으로 오진한다.
        failure_code = "CONTEXT_OVERFLOW" if finish_reason == "length" else "PROVIDER_ERROR"
        raise LlmError(
            f"{failure_code}: 응답 content가 비어 있습니다 (finishReason={finish_reason})",
            {**usage, "status": "FAILED", "failure_code": failure_code},
        )

    return LlmResult(content=content, usage=usage, raw=body)