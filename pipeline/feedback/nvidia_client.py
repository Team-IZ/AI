"""Chat-completion client for NVIDIA Build's OpenAI-compatible API,
rotating across a pool of API keys so the effective rate limit scales
with the number of keys in the pool (see nvidia_key_pool.py).

Vendored verbatim from github.com/popixoxipop-collab/nvidia-build (src/nvidia_client.py).
See D56 in generate_questions.py / README.md for why this is a copy, not a package
dependency. If you change rotation/retry behavior, update the source repo first,
then re-copy here. (Last synced: nvidia-build commit 6b57963, D11 per-model fix.)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from nvidia_key_pool import NvidiaKeyPool

try:
    # D98: centralized timeout (repo root, not part of the vendored upstream --
    # falls back to a literal if this file is ever copied somewhere without it,
    # e.g. back into nvidia-build).
    from timeout_config import DEFAULT_TIMEOUT_S
except ImportError:
    DEFAULT_TIMEOUT_S = 600.0

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


class NvidiaRotatingClient:
    # D98: default bumped 120s -> 600s (user request, global default) after
    #   llama-3.3-70b-instruct's worker-queue overload (503 "153/16", see D94)
    #   showed real single-call latency up to ~300s+ under load; 120s was
    #   timing out calls that would have succeeded. This is vendored code
    #   (D56 -- normally update nvidia-build upstream first, then re-copy) but
    #   the user explicitly asked for the global default, so applied here
    #   directly; sync upstream is still open.
    def __init__(self, pool: NvidiaKeyPool | None = None, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.pool = pool or NvidiaKeyPool.from_env()
        self.timeout_s = timeout_s

    def chat(self, model: str, messages: list[dict], max_retries: int = 3, **kwargs) -> dict:
        """POST /chat/completions using a rotated key. Returns the parsed JSON body.

        On HTTP 429 from the key that was picked, that call doesn't count
        against any other key's budget: we just acquire the next available
        key and retry, up to max_retries.
        """
        last_error: Exception | None = None
        for attempt in range(max_retries):
            key = self.pool.acquire(model)
            body = json.dumps({"model": model, "messages": messages, **kwargs}).encode()
            req = urllib.request.Request(
                API_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_retries - 1:
                    last_error = e
                    continue  # try the next key in the pool
                raise
            except urllib.error.URLError as e:
                # never reached the server; don't burn this key's budget slot
                self.pool.release_on_failure(key, model)
                last_error = e
                if attempt < max_retries - 1:
                    continue
                raise
        raise last_error  # pragma: no cover — unreachable, satisfies type-checkers
