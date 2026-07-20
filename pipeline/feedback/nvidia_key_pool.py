"""Rate-aware rotation across multiple NVIDIA Build API keys.

NVIDIA Build's free tier caps each API key at 40 requests/minute *per model*
(https://build.nvidia.com). Pooling N independently-issued keys and routing
each call to whichever key currently has the most headroom raises the
practical ceiling to N x 40 RPM per model without exceeding any single key's
limit for that model.

This module only tracks and enforces the sliding-window budget; it does not
make HTTP calls itself (see nvidia_client.py for the calling wrapper).

Vendored verbatim from github.com/popixoxipop-collab/nvidia-build (src/nvidia_key_pool.py).
See D56 in generate_questions.py / README.md for why this is a copy, not a package
dependency. If you change rotation/retry behavior, update the source repo first,
then re-copy here. (Last synced: nvidia-build commit 6b57963, D11 per-model fix.)
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class _KeyState:
    key: str
    # D11: budget tracked per model, not one shared bucket for the whole key
    #   WHY: NVIDIA's 40/min cap is per (key, model) pair, not per key overall.
    #        A single shared bucket meant that driving many *different*
    #        models through one key (e.g. benchmarking 87 models with 1 key)
    #        made the pool think the key was saturated after only 40 calls
    #        total, even though each individual model was nowhere near its
    #        own real 40/min ceiling. Discovered when a live survey against
    #        87 models logged "All 1 keys saturated" / HTTP 429 for slow
    #        models whose calls piled up behind faster ones sharing the same
    #        bucket -- not a real server-side limit, a client-side undercount.
    #   COST: memory grows with the number of distinct models seen (each gets
    #         its own deque); irrelevant in practice (a handful of model IDs).
    #   EXIT: if NVIDIA's limit is ever confirmed to be per-key-total instead
    #         of per-(key,model), collapse this back to a single deque.
    timestamps_by_model: dict = field(default_factory=lambda: defaultdict(deque))


class KeyPoolExhausted(RuntimeError):
    """Raised when no key gets capacity within the configured max wait."""


class NvidiaKeyPool:
    """Round-robins across API keys, respecting a per-(key, model) requests/window budget.

    Thread-safe: safe to share one instance across concurrent callers.
    """

    def __init__(self, keys: list[str], capacity_per_minute: int = 40, window_s: float = 60.0):
        if not keys:
            raise ValueError("NvidiaKeyPool requires at least one API key")
        self._states = [_KeyState(key=k) for k in keys]
        self._capacity = capacity_per_minute
        self._window_s = window_s
        self._lock = threading.Lock()
        # D94c: fairer round-robin start point so low-concurrency long jobs can
        # actually spread calls across NVIDIA_API_KEY_1..N instead of pinning to
        # the first key until it saturates.
        self._next_index = 0

    @classmethod
    def from_env(cls, prefix: str = "NVIDIA_API_KEY_", capacity_per_minute: int = 40) -> "NvidiaKeyPool":
        """Load NVIDIA_API_KEY_1, NVIDIA_API_KEY_2, ... (any count, any order) from env.

        # D2: indexed NVIDIA_API_KEY_<N> vars instead of one comma-separated var
        #   WHY:  each teammate owns exactly one line in .env -- easy to see
        #         whose key is whose, add/remove a teammate without touching
        #         anyone else's line, and no CSV-escaping edge cases.
        #   COST: N env vars instead of 1; slightly more verbose .env file.
        #   EXIT: replace this method's body with
        #         `os.environ["NVIDIA_API_KEYS"].split(",")` if the team
        #         later prefers a single list.
        """
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        found = []
        for name, value in os.environ.items():
            m = pattern.match(name)
            if m and value.strip():
                found.append((int(m.group(1)), value.strip()))
        if not found:
            raise ValueError(
                f"No environment variables matching {prefix}<N> were found. "
                f"Copy .env.example to .env and fill in your team's keys."
            )
        found.sort(key=lambda pair: pair[0])
        keys = [v for _, v in found]
        return cls(keys, capacity_per_minute=capacity_per_minute)

    def __len__(self) -> int:
        return len(self._states)

    @property
    def theoretical_max_rpm(self) -> int:
        """Max requests/minute *per model* achievable across the whole pool."""
        return len(self._states) * self._capacity

    def _prune(self, timestamps: deque, now: float) -> None:
        cutoff = now - self._window_s
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def acquire(self, model: str, max_wait_s: float = 30.0) -> str:
        """Return an API key with available capacity *for this model*, reserving a slot.

        Blocks (briefly) if every key is currently saturated for `model`,
        waking up as soon as the least-loaded key's oldest request for that
        model ages out of the window.

        # D1: per-key sliding-window budget instead of naive round-robin
        #   WHY:  round-robin alone doesn't know a key's actual remaining
        #         budget, so bursts can 429 a key well before others are used.
        #         Tracking real timestamps lets every key be driven right up
        #         to (not over) its 40/min limit.
        #   COST: O(keys) lock-held scan per acquire() instead of an O(1)
        #         pointer increment; negligible at pool sizes of a few teams.
        #   EXIT: swap the body of acquire() for a plain itertools.cycle() if
        #         the timestamp bookkeeping is ever not worth the complexity.
        """
        deadline = time.monotonic() + max_wait_s
        while True:
            with self._lock:
                now = time.time()
                best_wait = None
                n_states = len(self._states)
                for offset in range(n_states):
                    idx = (self._next_index + offset) % n_states
                    state = self._states[idx]
                    timestamps = state.timestamps_by_model[model]
                    self._prune(timestamps, now)
                    if len(timestamps) < self._capacity:
                        timestamps.append(now)
                        self._next_index = (idx + 1) % n_states
                        return state.key
                    wait = timestamps[0] + self._window_s - now
                    if best_wait is None or wait < best_wait:
                        best_wait = wait
            if time.monotonic() >= deadline:
                raise KeyPoolExhausted(
                    f"All {len(self._states)} keys saturated for model {model!r} "
                    f"({self._capacity}/min each); gave up after {max_wait_s}s"
                )
            time.sleep(max(0.05, min(best_wait or 0.5, deadline - time.monotonic())))

    def release_on_failure(self, key: str, model: str) -> None:
        """Undo the reservation for `key`+`model` when the call never reached the
        server (e.g. connection error) so it doesn't count against that budget."""
        with self._lock:
            for state in self._states:
                if state.key == key:
                    timestamps = state.timestamps_by_model[model]
                    if timestamps:
                        timestamps.pop()
                    return
