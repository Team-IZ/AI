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

# 호출 1건 상한. 원본 기본값은 600초인데 그건 과부하 모델까지 기다리는 값이라
# 우리 배치에는 너무 길다 — 굳은 호출 하나가 분석 전체를 붙잡는다.
DEFAULT_TIMEOUT_S = 120.0

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