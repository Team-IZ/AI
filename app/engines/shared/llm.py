""" NVIDIA Build 채팅 완성 호출의 유일한 통로 -- HTTP를 직접 만지는 유일한 모듈

D4 (2026-07-30): tools/lint_llm_calls.py::ALLOWLIST_LLM001에 이 파일 하나만
등록돼 있다. 다른 곳에서 requests.post/httpx.post/OpenAI SDK를 직접 부르면
린터가 잡는다 -- 그러니 "이 계측을 거치지 않은 LLM 호출"이 조용히 생길 수 없다.

shared/llm.js(origin/feat/poc_full)의 행동을 그대로 옮기되, 브라우저 특유의
두 가지는 의도적으로 뺀다:
  - submit-and-poll 작업 큐(D-H): CORS 우회를 위한 Cloudflare Worker 프록시
    아키텍처 전용 장치다. 이 서비스는 서버 프로세스라 NVIDIA를 직접 부르면
    되고, CORS 자체가 해당 없다.
  - NvidiaKeyPool 로테이션(D9): 사용자가 로컬 테스트 편의로 만든 것. 실배포는
    40 rpm 단일 키(app.engines.shared.secrets.nvidia_api_key())를 그대로 쓴다.
    round 단위 재시도 조정(D169)도 여기서는 안 옮긴다 -- max_attempts 하나로
    충분하다(여러 호출에 걸친 조율이 필요할 만큼 동시성이 높은 상황이 아님).

옮기는 것 셋:
  - content/reasoning_content 폴백(D131/D142): 일부 모델이 JSON 모드 응답을
    content가 아니라 reasoning_content에 남긴다.
  - finish_reason 항상 반환(D158): 응답이 max_tokens에서 잘렸는지 호출자가
    추측 없이 알 수 있어야 한다.
  - 모델별 reasoning_effort 화이트리스트(D217): 이 파라미터를 지원 안 하는
    모델(Mistral 등)에 보내면 하드 400이 난다 -- 목록에 없으면 절대 안 보낸다.

재시도 백오프: 2026-07-08 Code_reviewer_with_feedback의 실측 교훈(nvidia_client.py의
chat()이 429에도 지연 없이 즉시 재시도해, 동시성과 겹쳐 순간 처리율이 레이트리밋을
실제로 넘겼을 가능성) -- 여기서는 시도 사이에 time.sleep(2**attempt) 지수 백오프를
반드시 둔다(테스트는 monkeypatch로 time.sleep 자체를 무력화해 빠르게 돈다).
"""
from __future__ import annotations

import json as json_module
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import httpx

from app.engines.shared.secrets import nvidia_api_key

NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"

# D217: 이 목록에 없는 모델에는 reasoning_effort를 절대 보내지 않는다 --
# Mistral 계열은 지원하지 않는 값을 받으면 하드 400을 낸다(확인된 사실, 추측 아님).
REASONING_EFFORT_BY_MODEL: dict[str, str] = {
    "stepfun-ai/step-3.7-flash": "low",
}


class LlmCallError(Exception):
    """ failure_code가 AiUsageEntry.failure_code의 5개 값 중 하나와 정확히 일치한다 """

    failure_code: str = "PROVIDER_ERROR"


class LlmTimeoutError(LlmCallError):
    failure_code = "TIMEOUT"


class LlmRateLimitedError(LlmCallError):
    failure_code = "RATE_LIMITED"


class LlmProviderError(LlmCallError):
    failure_code = "PROVIDER_ERROR"


class LlmInvalidJsonError(LlmCallError):
    failure_code = "INVALID_JSON"


class LlmContextOverflowError(LlmCallError):
    failure_code = "CONTEXT_OVERFLOW"


@dataclass(frozen=True)
class ChatResult:
    content: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    tool_calls: tuple[Mapping[str, Any], ...] = ()


Transport = Callable[..., httpx.Response]


def _default_transport(url: str, *, json: dict, headers: dict, timeout: float) -> httpx.Response:
    return httpx.post(url, json=json, headers=headers, timeout=timeout)


def reasoning_effort_for(model_code: str) -> str | None:
    return REASONING_EFFORT_BY_MODEL.get(model_code)


def chat(
    *,
    model_code: str,
    messages: Sequence[Mapping[str, str]],
    max_tokens: int,
    temperature: float = 0.0,
    json_mode: bool = True,
    tools: Sequence[Mapping[str, Any]] | None = None,
    timeout_s: float = 600.0,
    max_attempts: int = 3,
    api_key: str | None = None,
    transport: Transport = _default_transport,
) -> ChatResult:
    """ NVIDIA Build 채팅 완성 1건. 실패하면 5개 failure_code 중 하나로 매핑된 LlmCallError 발생

    재시도 가치가 있는 실패(TIMEOUT/RATE_LIMITED/PROVIDER_ERROR)만 max_attempts까지
    지수 백오프(time.sleep(2**attempt))로 다시 시도한다. INVALID_JSON/CONTEXT_OVERFLOW는
    같은 입력을 다시 보내도 같은 결과이므로 즉시 올린다(재시도 낭비 방지).
    """
    key = api_key or nvidia_api_key()
    body: dict[str, Any] = {
        "model": model_code,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    effort = reasoning_effort_for(model_code)
    if effort:
        body["reasoning_effort"] = effort
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = list(tools)

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    last_error: LlmCallError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _send_once(transport, body, headers, timeout_s)
        except (LlmTimeoutError, LlmRateLimitedError, LlmProviderError) as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(2 ** attempt)  # 지수 백오프 -- 무지연 즉시재시도 금지(위 docstring 참고)
            continue
        except (LlmInvalidJsonError, LlmContextOverflowError):
            raise  # 재시도해도 같은 결과일 실패는 즉시 올린다

    assert last_error is not None
    raise last_error


def _send_once(transport: Transport, body: dict, headers: dict, timeout_s: float) -> ChatResult:
    try:
        response = transport(NVIDIA_ENDPOINT, json=body, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise LlmTimeoutError(str(exc)) from exc
    except httpx.RequestError as exc:
        raise LlmProviderError(str(exc)) from exc

    if response.status_code == 429:
        raise LlmRateLimitedError(f"HTTP 429: {response.text[:300]}")
    if response.status_code == 400 and "context" in response.text.lower():
        raise LlmContextOverflowError(f"HTTP 400 (context): {response.text[:300]}")
    if response.status_code >= 400:
        raise LlmProviderError(f"HTTP {response.status_code}: {response.text[:300]}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise LlmProviderError(f"예상치 못한 응답 형태(choices 없음): {str(data)[:300]}")
    message = choices[0].get("message") or {}
    finish_reason = choices[0].get("finish_reason", "")

    # D131/D142: 일부 모델은 JSON 모드 응답을 content가 아니라 reasoning_content에 남긴다
    content = message.get("content") or message.get("reasoning_content") or ""
    tool_calls = tuple(message.get("tool_calls", []) or ())
    if not content and not tool_calls:
        raise LlmInvalidJsonError(f"빈 응답 (finish_reason={finish_reason})")

    usage = data.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return ChatResult(
        content=content,
        finish_reason=finish_reason,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cached_tokens=cached,
        tool_calls=tool_calls,
    )


_FENCE_OPEN_RE = re.compile(r"^```(?:json)?\s*")
_FENCE_CLOSE_RE = re.compile(r"\s*```$")
_BRACE_SLICE_RE = re.compile(r"\{[\s\S]*\}")


def extract_json_object(text: str) -> dict[str, Any]:
    """ shared/llm.js::extractJsonObject 이식 -- 코드펜스 제거 후 파싱, 실패하면
    첫 '{'부터 마지막 '}'까지만 잘라 재시도. 그래도 안 되면 LlmInvalidJsonError. """
    cleaned = (text or "").strip()
    cleaned = _FENCE_OPEN_RE.sub("", cleaned)
    cleaned = _FENCE_CLOSE_RE.sub("", cleaned)
    try:
        return json_module.loads(cleaned)
    except ValueError:
        pass

    m = _BRACE_SLICE_RE.search(cleaned)
    if not m:
        raise LlmInvalidJsonError(f"JSON을 찾을 수 없음: {cleaned[:200]}")
    try:
        return json_module.loads(m.group(0))
    except ValueError as exc:
        raise LlmInvalidJsonError(f"JSON 파싱 실패: {cleaned[:200]}") from exc
