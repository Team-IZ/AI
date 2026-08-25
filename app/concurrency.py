""" 무거운 배치성 job(코드분석·교안분석)이 함께 쓰는 전역 CPU 예산.

D4 continued(2026-08-25, jobs.py/config.py의 D4 주석과 같은 결정):
코드분석과 교안분석은 job 하나당 ThreadPoolExecutor(max_workers=8)을 똑같이 쓴다
(둘 다 hints.MAX_PARALLEL을 그대로 재사용) -- CPU 부담 프로파일이 같다.

이 세마포어를 jobs.py/curricula.py가 **하나만 공유**하는 이유: 각자 따로
Semaphore(6)을 들면, 최악의 경우 코드분석 6개 + 교안분석 6개 = 12개가 동시에
돌아 2026-08-25 인시던트(job 12개 동시 시작 → CPU 100% 고정, 헬스체크 15초
무응답)를 그대로 재현한다. 하나의 풀을 공유해야 "무거운 job 전체"가 6개를
넘지 않는다는 보장이 실제로 선다.

D-heavy-pool(2026-08-26, ECS Fargate 마이그레이션 Phase 3): 순수 세마포어를
HeavyJobPool로 교체한다.
  WHY: 오토스케일링이 스케일아웃 신호로 쓸 값이 세마포어 자체엔 없다 --
       in_flight는 상한(capacity)에서 정의상 포화돼 그 이상을 표현 못 한다
       (App Runner의 Concurrency 지표가 똑같은 이유로 스케일링 신호로 못 쓰였던
       함정을 그대로 반복하게 된다). **waiting**(세마포어 대기 중인 job 수)이
       진짜 신호다 -- 상한이 없고, 0보다 크다는 것 자체가 "이 태스크 용량
       부족"이라는 순수한 backpressure다.
       그리고 오토스케일링이 붙으면 스케일인이 진행 중인 job(최대 26분 관측)을
       죽일 수 있다 -- ECS Task Scale-In Protection으로 막는다(in_flight가
       0→1이 되는 순간 보호 ON, 1→0이 되는 순간 OFF).
  COST: 매 진입·이탈마다 락을 잡는 오버헤드가 붙는다(job 자체가 수십 초~수십
       분인 것에 비하면 무시할 수준). 태스크 보호 API 호출이 실패해도 job은
       계속 진행해야 한다 -- 관측/보호는 부가 기능이지 job 실행의 전제조건이
       아니다.
  EXIT: 해당 없음.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from app.config import get_settings

log = logging.getLogger(__name__)


class HeavyJobPool:
    """threading.Semaphore를 감싸 관측 카운터 + ECS 태스크 스케일인 보호를 더한다.

    `with pool:` 그대로 쓸 수 있다 -- tests/test_concurrency.py가 순수
    `threading.Semaphore`로 monkeypatch해도 여전히 통과한다(둘 다 context
    manager 프로토콜만 요구하므로), 이 클래스가 그 자리를 대체해도 기존 계약이
    안 깨진다.
    """

    def __init__(self, capacity: int):
        self._semaphore = threading.Semaphore(capacity)
        self._capacity = capacity
        self._state_lock = threading.Lock()
        self._in_flight = 0
        self._waiting = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def waiting(self) -> int:
        return self._waiting

    def __enter__(self) -> "HeavyJobPool":
        with self._state_lock:
            self._waiting += 1
        self._semaphore.acquire()
        with self._state_lock:
            self._waiting -= 1
            self._in_flight += 1
            became_busy = self._in_flight == 1
        if became_busy:
            _set_task_protection(True)
        return self

    def __exit__(self, *exc_info) -> None:
        with self._state_lock:
            self._in_flight -= 1
            became_idle = self._in_flight == 0
        if became_idle:
            _set_task_protection(False)
        self._semaphore.release()


def _set_task_protection(enabled: bool) -> None:
    """ECS Fargate 태스크 스케일인 보호(§2 계획서). ECS 밖(App Runner·로컬
    개발)에서는 `ECS_AGENT_URI`가 주입되지 않아 조용히 아무 것도 안 한다.

    호출 실패가 job 실행 자체를 막으면 안 된다 -- 보호는 부가 기능이지
    전제조건이 아니라서 예외를 삼키고 경고만 남긴다.
    """
    agent_uri = os.environ.get("ECS_AGENT_URI", "")
    if not agent_uri:
        # D-diag(2026-08-26): ECS_AGENT_URI라는 이름을 계획서 조사 단계에서 문서를
        # 보고 가정했는데, 실제 배포에서 protectionInfo가 계속 null이라 확정이
        # 아니었음이 드러났다 -- 추측 대신 실제 컨테이너에 뭐가 있는지 로그로 남긴다.
        ecs_env = sorted(k for k in os.environ if "ECS" in k.upper())
        log.warning("task-protection 스킵: ECS_AGENT_URI 없음. 실제 ECS 관련 환경변수: %s",
                    ecs_env)
        return
    body = json.dumps({"ProtectionEnabled": enabled, "ExpiresInMinutes": 60}).encode()
    req = urllib.request.Request(
        f"{agent_uri}/task-protection/v1/state",
        data=body, method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            # D-diag2(2026-08-26): status=200이면서도 describe-tasks의
            # protectionInfo가 null로 남는 사례를 실측했다 -- ECS 태스크 보호
            # API는 200을 줘도 본문에 failure가 실릴 수 있어(문서화된 응답
            # 구조) 본문을 확인해야 진짜 성공인지 알 수 있다.
            raw = resp.read()
            log.info("task-protection(enabled=%s) 요청 성공 status=%s body=%s",
                      enabled, resp.status, raw.decode(errors="replace")[:500])
    except (urllib.error.URLError, OSError) as exc:
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = f" body={exc.read().decode(errors='replace')[:300]}"
            except Exception:
                pass
        log.warning("task-protection(enabled=%s) 요청 실패, 무시하고 진행: %s%s", enabled, exc, detail)


def _emf_loop(pool: HeavyJobPool, interval_s: float) -> None:
    while True:
        time.sleep(interval_s)
        # EMF(Embedded Metric Format): CloudWatch Logs로 나가는 이 한 줄짜리 JSON을
        # CloudWatch가 자동으로 커스텀 지표로 추출한다 -- boto3/PutMetricData 없이도
        # 되므로 fetch.py의 SSRF 방어 논거(app/job_store.py의 DynamoDbJobStore
        # docstring 참고)를 이 이상 더 좁히지 않는다. print()가 전부다.
        try:
            print(json.dumps({
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [{
                        "Namespace": "TeamIZ/AI",
                        "Dimensions": [["ServiceName"]],
                        "Metrics": [
                            {"Name": "HeavyJobsWaiting", "Unit": "Count"},
                            {"Name": "HeavyJobsInFlight", "Unit": "Count"},
                            {"Name": "HeavyJobsCapacity", "Unit": "Count"},
                        ],
                    }],
                },
                "ServiceName": "teamiz-ai",
                "HeavyJobsWaiting": pool.waiting,
                "HeavyJobsInFlight": pool.in_flight,
                "HeavyJobsCapacity": pool.capacity,
            }), flush=True)
        except Exception:  # 지표 발행 실패로 서비스가 죽으면 안 된다
            log.exception("EMF 지표 발행 실패")


def start_emf_reporter(interval_s: float = 30.0) -> None:
    """`HEAVY_JOB_CONCURRENCY`의 waiting/in_flight/capacity를 주기적으로
    CloudWatch 커스텀 지표(TeamIZ/AI 네임스페이스)로 내보낸다. app/main.py가
    기동 시 한 번 부른다. 데몬 스레드라 프로세스 종료를 막지 않는다.
    """
    threading.Thread(target=_emf_loop, args=(HEAVY_JOB_CONCURRENCY, interval_s), daemon=True).start()


# analysis_max_concurrent_jobs라는 이름은 코드분석 인시던트에서 나왔지만,
# 이 세마포어는 그 이름이 가리키는 것보다 넓게(교안분석까지) 적용된다 -- 필드명을
# 다시 붙이는 비용보다, 여기 이 주석으로 실제 범위를 명확히 하는 쪽을 택했다.
HEAVY_JOB_CONCURRENCY = HeavyJobPool(get_settings().analysis_max_concurrent_jobs)
