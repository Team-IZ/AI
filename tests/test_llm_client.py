""" LLM 래퍼(T7a). 네트워크 없이 응답 해석·실패 분류만 검증한다. """
import io
import os
import urllib.error

import pytest

from app.llm import client


def _body(content, reasoning="생각 중...", finish="stop", usage=None):
    return {
        "choices": [{"message": {"content": content, "reasoning_content": reasoning},
                     "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


class _FakeClient:
    def __init__(self, body):
        self._body = body

    def __call__(self, pool=None, timeout_s=None):
        return self

    def chat(self, model, messages, **kwargs):
        return self._body


@pytest.fixture
def fake_vendor(monkeypatch):
    """vendor 대신 가짜를 꽂는다. 키도 네트워크도 안 쓴다."""
    def _install(body):
        fake = _FakeClient(body)
        monkeypatch.setattr(client, "_pool", object())
        monkeypatch.setattr(client, "_load_vendor", lambda: (fake, None, _Exhausted))
    return _install


class _Exhausted(Exception):
    pass


def test_content_wins_over_reasoning(fake_vendor):
    """답은 content다. reasoning_content로 대체하면 사고 과정이 답으로 새어나간다."""
    fake_vendor(_body("실제 답"))

    r = client.chat("m", [{"role": "user", "content": "q"}])

    assert r.content == "실제 답"
    assert r.usage["status"] == "SUCCEEDED"


def test_truncated_output_is_an_error(fake_vendor):
    """출력이 잘려 content가 비면 실패다 — 빈 문자열을 조용히 돌려주면
    다음 단계가 "모델이 JSON을 안 줬다"로 오진한다."""
    fake_vendor(_body("", finish="length"))

    with pytest.raises(client.LlmError) as exc:
        client.chat("m", [{"role": "user", "content": "q"}])

    assert exc.value.usage["failure_code"] == "CONTEXT_OVERFLOW"
    assert exc.value.usage["status"] == "FAILED"


def test_tokens_are_extracted(fake_vendor):
    """비용의 근거값이다. cached는 없을 수도 있으니 0으로 떨어져야 한다."""
    fake_vendor(_body("답", usage={"prompt_tokens": 100, "completion_tokens": 20,
                                   "prompt_tokens_details": {"cached_tokens": 30}}))

    u = client.chat("m", [{"role": "user", "content": "q"}]).usage

    assert (u["input_token_count"], u["output_token_count"], u["cached_token_count"]) == (100, 20, 30)


@pytest.mark.parametrize("code", [429, 503, 529])
def test_transient_provider_codes_are_rate_limited(code):
    """실패 코드가 틀리면 Spring이 재시도할지 포기할지 잘못 판단한다.

    🔴 503은 2026-08-10에 합류했다. 진행 로그를 켜자마자 실패 대부분이
    `503 ResourceExhausted: Worker local total request limit reached (202/32)`였는데,
    529와 성격이 같은 큐 포화인데도 PROVIDER_ERROR로 들어가 진짜 장애와 섞였다.
    백엔드는 PROVIDER_ERROR를 MODEL_ERROR로 저장해서 모델 탓으로 기록된다.
    """
    err = urllib.error.HTTPError("url", code, "Service Unavailable", {}, None)

    assert client._classify(err, str(err)) == "RATE_LIMITED"


@pytest.mark.parametrize("status,expected", [
    (None, True),    # HTTP 응답 자체가 없었다(네트워크·타임아웃)
    (500, True),
    (503, True),     # 워커 포화
    (529, True),
    (408, True),
    (429, True),
    (400, False),
    (401, False),    # 키 거부 — 다음에도 같다
    (403, False),
    (404, False),    # 모델 없음 — 실측에서 이걸 6번 던졌다
])
def test_only_transient_statuses_are_retried(status, expected):
    """🔴 2026-08-10 실측 회귀 — 404를 6번 던지고 12초를 버렸다.

    백엔드가 providerModelCode에 Swagger 기본값 `"string"`을 실어 보내
    `404 page not found`가 왔는데, 실패 코드가 PROVIDER_ERROR라 재시도 대상에
    들어갔다. 모델이 없다는 답은 다시 물어도 같다.
    """
    assert client.is_retryable(status) is expected


def test_llm_error_carries_the_http_status(monkeypatch):
    """재시도 판단의 재료다 — 예외에 안 실리면 stages가 상태코드를 못 본다."""
    err = urllib.error.HTTPError("url", 404, "Not Found", {}, io.BytesIO(b"404 page not found"))
    monkeypatch.setattr(client, "_pool", object())
    monkeypatch.setattr(client, "_load_vendor", lambda: (_FailingClient(err), None, _Exhausted))

    with pytest.raises(client.LlmError) as exc:
        client.chat("string", [{"role": "user", "content": "q"}])

    assert exc.value.status_code == 404
    assert client.is_retryable(exc.value.status_code) is False


def test_real_provider_failure_is_not_rate_limited():
    """일시적 포화와 진짜 장애는 갈려야 한다 — 안 그러면 실패 통계를 못 읽는다."""
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    assert client._classify(err, str(err)) == "PROVIDER_ERROR"


class _FailingClient(_FakeClient):
    def __init__(self, exc):
        self._exc = exc

    def chat(self, model, messages, **kwargs):
        raise self._exc


def test_http_status_code_survives_into_the_error_message(monkeypatch):
    """🔴 2026-08-10 배포본 장애 회귀 — 상태코드를 버려서 원인을 못 읽었다.

    호출 4회가 전부 `PROVIDER_ERROR: HTTPError`로만 죽어서, 401(키 거부)·404(모델 없음)·
    402(크레딧 소진) 중 무엇인지 가릴 수 없었다. 예외 종류만 남기면 진단이 불가능하다.
    """
    err = urllib.error.HTTPError(
        "url", 401, "Unauthorized", {},
        io.BytesIO(b'{"detail":"invalid key nvapi-SECRET123"}'),
    )
    monkeypatch.setattr(client, "_pool", object())
    monkeypatch.setattr(client, "_load_vendor", lambda: (_FailingClient(err), None, _Exhausted))

    with pytest.raises(client.LlmError) as exc:
        client.chat("m", [{"role": "user", "content": "q"}])

    msg = str(exc.value)
    assert "401" in msg and "Unauthorized" in msg
    assert exc.value.usage["failure_code"] == "PROVIDER_ERROR"
    # 본문은 싣되 키는 절대 안 싣는다.
    assert "nvapi-SECRET123" not in msg
    assert "nvapi-[REDACTED]" in msg
    

def test_env_file_keys_reach_the_pool(tmp_path, monkeypatch):
    """`.env`에 키를 넣어도 풀이 못 찾던 버그(2026-08-02 실호출에서 발견).

    pydantic-settings는 .env를 읽어 Settings만 채우고 os.environ은 안 건드린다.
    그런데 vendor의 NvidiaKeyPool.from_env()는 os.environ만 본다 — 로컬 실행에서
    키가 영영 안 보인다. AWS는 진짜 환경변수라 안 터지지만 로컬이 운영 계획의 절반이다.
    """
    from app import config

    env = tmp_path / ".env"
    env.write_text('NVIDIA_API_KEY_2=second\nNVIDIA_API_KEY_1="first"\nAPP_ENV=local\n',
                   encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    config.load_api_keys_into_env.cache_clear()
    monkeypatch.delenv("NVIDIA_API_KEY_1", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY_2", raising=False)

    assert config.load_api_keys_into_env() == 2
    assert os.environ["NVIDIA_API_KEY_1"] == "first"    # 따옴표는 벗긴다
    assert os.environ["NVIDIA_API_KEY_2"] == "second"

    config.load_api_keys_into_env.cache_clear()


def test_real_env_wins_over_the_env_file(tmp_path, monkeypatch):
    """배포 환경의 값이 저장소 .env보다 우선해야 한다 — 반대면 운영 키가 밀린다."""
    from app import config

    env = tmp_path / ".env"
    env.write_text("NVIDIA_API_KEY_1=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setenv("NVIDIA_API_KEY_1", "from-deployment")
    config.load_api_keys_into_env.cache_clear()

    assert config.load_api_keys_into_env() == 0
    assert os.environ["NVIDIA_API_KEY_1"] == "from-deployment"

    config.load_api_keys_into_env.cache_clear()
