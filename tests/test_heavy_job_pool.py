""" HeavyJobPool 검증 -- 관측 카운터(waiting/in_flight/capacity)와
ECS 태스크 스케일인 보호 호출. app/concurrency.py의 D-heavy-pool 주석 참고.
"""

import threading
import time

import pytest

from app.concurrency import HeavyJobPool


def test_capacity_is_fixed_at_construction():
    pool = HeavyJobPool(4)
    assert pool.capacity == 4
    assert pool.in_flight == 0
    assert pool.waiting == 0


def test_in_flight_tracks_concurrent_holders(monkeypatch):
    """capacity 안에서는 in_flight가 그대로 늘고, waiting은 0이다."""
    monkeypatch.setattr("app.concurrency._set_task_protection", lambda enabled: None)
    pool = HeavyJobPool(3)
    all_entered = threading.Barrier(3)
    all_observed = threading.Barrier(3)  # 관측 끝날 때까지 아무도 먼저 나가지 않게
    seen_in_flight = []
    lock = threading.Lock()

    def worker():
        with pool:
            all_entered.wait(timeout=5)  # 3개 다 잡을 때까지 기다렸다가 관측
            with lock:
                seen_in_flight.append(pool.in_flight)
            all_observed.wait(timeout=5)  # 관측 끝나기 전엔 아무도 release 안 함

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert seen_in_flight == [3, 3, 3]
    assert pool.in_flight == 0  # 전부 빠져나간 뒤


def test_waiting_counts_jobs_blocked_past_capacity(monkeypatch):
    """capacity를 넘는 job은 waiting으로 잡혀야 한다 -- 오토스케일링 신호의
    핵심(in_flight는 상한에서 포화돼 그 이상을 못 나타내지만 waiting은 안 그렇다)."""
    monkeypatch.setattr("app.concurrency._set_task_protection", lambda enabled: None)
    pool = HeavyJobPool(1)
    release_event = threading.Event()
    entered_event = threading.Event()

    def holder():
        with pool:
            entered_event.set()
            release_event.wait(timeout=5)

    def waiter():
        with pool:
            pass

    t_holder = threading.Thread(target=holder)
    t_holder.start()
    entered_event.wait(timeout=5)

    t_waiter = threading.Thread(target=waiter)
    t_waiter.start()
    time.sleep(0.1)  # waiter가 acquire()에서 블록될 시간을 준다

    assert pool.waiting == 1
    assert pool.in_flight == 1

    release_event.set()
    t_holder.join(timeout=5)
    t_waiter.join(timeout=5)
    assert pool.waiting == 0
    assert pool.in_flight == 0


def test_task_protection_enabled_on_first_entry_disabled_on_last_exit(monkeypatch):
    """0->1 진입에서만 ON, 1->0 이탈에서만 OFF -- 중간에 여러 job이 겹쳐도
    보호 API를 한 번씩만 부른다(불필요한 호출 반복 방지)."""
    calls = []
    monkeypatch.setattr("app.concurrency._set_task_protection", lambda enabled: calls.append(enabled))

    pool = HeavyJobPool(3)
    with pool:
        assert calls == [True]
        with pool:
            assert calls == [True]  # 두 번째 진입은 추가 호출 없음
        assert calls == [True]  # 아직 하나 살아있으니 OFF 안 함
    assert calls == [True, False]


def test_set_task_protection_noop_without_ecs_agent_uri(monkeypatch):
    """ECS 밖(App Runner·로컬)에서는 ECS_AGENT_URI가 없어 아무 요청도 안 나가야 한다."""
    monkeypatch.delenv("ECS_AGENT_URI", raising=False)
    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append((a, k)))

    from app.concurrency import _set_task_protection
    _set_task_protection(True)

    assert calls == []


def test_set_task_protection_sends_put_to_ecs_agent_uri(monkeypatch):
    monkeypatch.setenv("ECS_AGENT_URI", "http://169.254.170.2/v1/abc123")
    requests = []

    class _FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"{}"

    def _fake_urlopen(req, timeout=None):
        requests.append(req)
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    from app.concurrency import _set_task_protection
    _set_task_protection(True)

    assert len(requests) == 1
    assert requests[0].full_url == "http://169.254.170.2/v1/abc123/task-protection/v1/state"
    assert requests[0].get_method() == "PUT"


def test_set_task_protection_failure_does_not_raise(monkeypatch):
    """보호 API가 실패해도(네트워크 오류 등) job 실행을 막으면 안 된다."""
    monkeypatch.setenv("ECS_AGENT_URI", "http://169.254.170.2/v1/abc123")

    def _raise(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    from app.concurrency import _set_task_protection
    _set_task_protection(True)  # 예외가 안 올라와야 한다
