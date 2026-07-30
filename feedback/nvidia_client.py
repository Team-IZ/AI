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
import time
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

# D223: a live 21-model benchmark run showed 53/82 (65%) of chunk-analysis
# failures were HTTPError(503)/TimeoutError -- and neither was retried at all
# before this fix (503 fell through the 429-only check below to `raise`; a
# bare read-phase TimeoutError isn't a urllib.error.URLError subclass, so it
# wasn't even caught). NVIDIA's own forums confirm this exact 503 ("Worker
# local total request limit reached") is an explicitly-transient, model-level
# shared-capacity overload -- not a per-key problem 429 rotation would fix --
# and is meant to be retried after a brief wait:
# https://forums.developer.nvidia.com/t/resourceexhausted-worker-local-total-request-limit-reached-33-32/375518
RETRYABLE_SERVER_STATUS = {500, 502, 503, 504}
RETRY_BACKOFF_BASE_S = 2.0


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
        key and retry, up to max_retries. On a 5xx or timeout (D223: shared
        server-side overload, not a per-key limit -- switching keys doesn't
        help), back off briefly before retrying instead.
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
                if e.code in RETRYABLE_SERVER_STATUS and attempt < max_retries - 1:
                    last_error = e
                    time.sleep(RETRY_BACKOFF_BASE_S * (2 ** attempt))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                # D223: a bare read-phase timeout raises TimeoutError, which is
                # a sibling of URLError (both subclass OSError), not a subclass
                # of it -- so it used to fall through both except clauses here
                # entirely uncaught, with zero retries. Treat it the same as
                # URLError: never got a usable response, so don't burn this
                # key's budget slot, and back off in case it's the same shared
                # overload as the 5xx case above.
                self.pool.release_on_failure(key, model)
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(RETRY_BACKOFF_BASE_S * (2 ** attempt))
                    continue
                raise
        raise last_error  # pragma: no cover — unreachable, satisfies type-checkers
