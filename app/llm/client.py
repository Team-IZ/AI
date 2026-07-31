""" LLM 호출 래퍼. vendor/ 의 NVIDIA 클라이언트를 감싸고 ai_usage 원장을 만든다.

여기는 우리 소유고 vendor/ 는 상류(nvidia-build) 소유다(vendor/SOURCE.md).
이 파일이 더하는 것: 키 풀 싱글턴 · 지연 측정 · 토큰 추출 · 실패 코드 분류.

실패해도 원장은 남아야 한다 — 실패한 호출도 토큰을 태우고 비용이 든다.
그래서 예외에 usage를 붙여 던진다(LlmError.usage).
"""

import sys
import threading
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_VENDOR = Path(__file__).parent / "vendor"

# 배치용 상한. 팀원 실측(shared/llm.js:143~147): step-3.7-flash는 reasoning_effort=low
# 로도 실제 프롬프트에서 39초가 걸렸고 재시도가 120초에서 통째로 타임아웃했다.
# 상류 vendor 기본값이 600초인 것도 그 때문이다 — 짧게 잡으면 성공할 호출을 죽인다.
# 분석 배치는 1시간 예산이라 이 값이 말이 된다.
DEFAULT_TIMEOUT_S = 600.0

# 세션용 상한. **학생이 화면 앞에서 기다리는 경로에는 배치 값을 쓰면 안 된다.**
#
# 실측(2026-07-31, mistral-medium-3.5): 채점 5.2 / 4.5 / 306.0초,
# 힌트 604.4(실패, HTTP 504) / 1.1 / 1.0초. **중앙값은 1~5초인데 긴꼬리가 5~10분이다.**
# 느린 모델이 아니라 공급자 쪽 일시적 멈춤이고, 600초 상한이 그 사고를 10분으로 키웠다.
#
# 중앙값이 5초인데 600초를 기다릴 이유가 없다. 짧게 끊고 다른 키로 재시도하면
# 604초짜리 사고가 60여 초로 줄어든다.
SESSION_TIMEOUT_S = 60.0

# reasoning_effort는 모델마다 지원 여부가 다르다. Mistral에 "low"를 보내면 무시가 아니라
# 하드 HTTP 400이다(팀원 실측). 그래서 전역 파라미터가 아니라 모델별 맵이어야 한다.
#
# step-3.7-flash에 "low"가 필요한 이유: 기본값(medium)이면 답 전체를 reasoning_content에만
# 쓰다가 content에 닿기 전에 max_tokens로 잘린다. "low"가 그 실패 모드를 없앤다.
# 지연 자체는 안 줄어든다 — 그건 위 타임아웃이 흡수한다.
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
}


def budget_for(model_code: str, manifest_max_tokens: int | None) -> int | None:
    """매니페스트의 max_tokens를 그 모델이 실제로 필요한 예산으로 환산한다."""
    if manifest_max_tokens is None:
        return None
    return int(manifest_max_tokens * REASONING_TOKEN_MULTIPLIER.get(model_code, 1.0))


_pool = None
_pool_lock = threading.Lock()


class LlmError(RuntimeError):
    """LLM 호출 실패. 원장에 남길 usage를 함께 들고 있다."""

    def __init__(self, message: str, usage: dict[str, Any]):
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True)
class LlmResult:
    content: str
    usage: dict[str, Any]   # AiUsage의 일부. featureCode·sourceId 등은 호출자가 채운다
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


def get_pool():
    """키 풀 싱글턴. 풀이 예산을 기억하므로 호출마다 새로 만들면 안 된다.

    새로 만들면 각 인스턴스가 자기 슬라이딩 윈도우만 봐서 실제 한도를
    N배로 초과한다 — 429가 나기 시작한다.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
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
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return "RATE_LIMITED"
    # 컨텍스트 초과는 400으로 오고 본문 문구로만 구분된다. 문구가 바뀌면
    # PROVIDER_ERROR로 떨어질 뿐이라 안전하게 실패한다.
    if "context length" in detail.lower() or "maximum context" in detail.lower():
        return "CONTEXT_OVERFLOW"
    return "PROVIDER_ERROR"


def chat(model_code: str, messages: list[dict], *, timeout_s: float = DEFAULT_TIMEOUT_S,
         **kwargs) -> LlmResult:
    """LLM 한 번 호출. 성공하면 LlmResult, 실패하면 LlmError(usage 포함).

    CPU가 아니라 네트워크 대기지만, 동기 호출이라 이벤트 루프에서 직접 부르면 안 된다.
    지금은 run_analysis가 동기 함수(`def`)라 Starlette이 threadpool로 돌려준다.
    """
    NvidiaRotatingClient, _, KeyPoolExhausted = _load_vendor()
    client = NvidiaRotatingClient(pool=get_pool(), timeout_s=timeout_s)
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
        body = client.chat(model_code, messages, **kwargs)
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        # 키 풀 포화는 우리가 던진 것이라 별도로 분류한다(429와 같은 뜻).
        detail = "" if isinstance(exc, KeyPoolExhausted) else str(exc)
        failure_code = "RATE_LIMITED" if isinstance(exc, KeyPoolExhausted) else _classify(exc, detail)
        usage = {**base, "status": "FAILED", "failure_code": failure_code, "latency_ms": latency_ms}
        # 예외 문구에 키가 실릴 일은 없지만(vendor가 Authorization을 로그에 안 남긴다)
        # 그래도 원문을 그대로 전파하지 않고 종류만 남긴다.
        raise LlmError(f"{failure_code}: {type(exc).__name__}", usage) from exc

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