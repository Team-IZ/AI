""" job 상태 저장소 추상화. jobs.py·curricula.py·reports.py 3형제가 공유한다.

왜 필요한가: 지금은 세 모듈 다 job 상태를 프로세스 인메모리 OrderedDict에
들고 있다. ECS Fargate로 옮기며 태스크를 2개 이상으로 늘리면, job을 만든
태스크와 그 뒤 폴링(GET)이 도착하는 태스크가 다를 수 있다 -- ALB는 라운드로빈일
뿐 "이 job을 아는 태스크로"를 모른다. 그러면 정상 진행 중인 job이 404
(JOB_NOT_FOUND)로 보이고, 백엔드는 연속 3회 실패를 "job 유실"로 판정해 이미
잘 되고 있는 분석을 새로 재시작시킨다(ECS Fargate 마이그레이션 계획 §0.1/§2,
2026-08-26 -- 태스크 수에 따라 오탐률 12.5%~58%로 계산됨). 이 파일은 그 블로커를
없애는 자리다: 저장소를 프로세스 밖(DynamoDB)으로 빼면 어느 태스크가 요청을
받아도 같은 상태를 본다.

인메모리 구현은 기존 jobs.py 동작을 한 글자도 안 바꾼다(dict에 객체 참조를
그대로 담아 mutate가 바로 보이는 방식 그대로) -- `JOB_STORE_BACKEND=dynamodb`를
명시적으로 켜야만 DynamoDB로 간다(app/config.py). 로컬 개발·단위테스트는 전부
인메모리 기본값을 그대로 쓰므로 AWS 자격증명이 없어도 동작한다.
"""

import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class JobStore(Protocol[T]):
    """jobs.py/curricula.py/reports.py가 실제로 쓰는 최소 인터페이스.

    핵심은 `save()`다. 인메모리 구현에선 사실 없어도 동작한다 -- dict 안의
    객체를 그 자리에서 mutate하면 get()이 이미 바뀐 값을 본다(참조 공유).
    그런데 DynamoDB 구현은 프로세스 경계를 넘어야 하니 명시적 쓰기가 필요하다.
    **`job.status`를 바꾼 뒤에는 항상 `save()`를 불러라** -- 안 그러면
    인메모리 백엔드(로컬·테스트)에서는 통과하는데 DynamoDB 백엔드(ECS 다중
    태스크)에서만 그 전이가 다른 태스크에 안 보이는, 로컬에서 재현이 안 되는
    버그가 된다.
    """

    def get(self, job_id: str) -> T | None: ...
    def create(self, job: T) -> None: ...
    def save(self, job: T) -> None: ...
    def get_id_by_idempotency_key(self, key: str) -> str | None: ...
    def set_idempotency_key(self, key: str, job_id: str) -> None: ...
    def delete_idempotency_key(self, key: str) -> None: ...


class InMemoryJobStore(Generic[T]):
    """기존 jobs.py 동작 그대로. OrderedDict + 상한(M9 redteam audit, 2026-08-05).

    `get()`이 dict에 들어있는 객체 참조를 그대로 돌려준다 -- 그래서
    `get_job(id) is job`(tests/test_jobs.py)이 성립한다. 이 identity가
    DynamoDbJobStore에선 절대 성립하지 않는다(매번 새로 역직렬화하므로) --
    두 백엔드가 이 지점에서 다르다는 걸 의도적으로 남겨둔다. 테스트는 기본값인
    이 백엔드로만 돈다.
    """

    def __init__(self, max_items: int = 2000):
        self._jobs: "OrderedDict[str, T]" = OrderedDict()
        self._idempotency: "OrderedDict[str, str]" = OrderedDict()
        self._max_items = max_items

    def get(self, job_id: str) -> T | None:
        return self._jobs.get(job_id)

    def create(self, job: T) -> None:
        self._jobs[job.job_id] = job
        while len(self._jobs) > self._max_items:
            self._jobs.popitem(last=False)

    def save(self, job: T) -> None:
        # 참조 공유라 이미 반영돼 있지만, 재대입해 두면 두 백엔드의 호출 규약이
        # 똑같아진다(호출자가 "이 백엔드는 save 없어도 되네"에 기대지 않게 됨).
        self._jobs[job.job_id] = job

    def get_id_by_idempotency_key(self, key: str) -> str | None:
        return self._idempotency.get(key)

    def set_idempotency_key(self, key: str, job_id: str) -> None:
        self._idempotency[key] = job_id
        while len(self._idempotency) > self._max_items:
            self._idempotency.popitem(last=False)

    def delete_idempotency_key(self, key: str) -> None:
        self._idempotency.pop(key, None)


class DynamoDbJobStore(Generic[T]):
    """ECS Fargate 다중 태스크용. `JOB_STORE_BACKEND=dynamodb`일 때만 만들어진다.

    job 전체를 `body`라는 단일 JSON 문자열 속성에 통째로 넣는다(DynamoDB의
    네이티브 Map 타입에 나눠 담지 않는다) -- Pydantic이 float를 그대로 뱉는데
    boto3 Table 리소스는 float를 거부한다(DynamoDB가 이진부동소수점을 안 써서
    Decimal 변환이 강제된다). 문자열로 감싸면 그 변환이 통째로 필요 없어진다 --
    JSON 인코딩은 float를 그냥 다룬다. 대신 부분 업데이트(UpdateItem으로 필드
    하나만 바꾸기)는 못 한다 -- `save()`가 항상 전체 덮어쓰기인 이유. job
    객체가 작고(payload 실측 몇 KB, DynamoDB 400KB 상한의 1%대 -- Phase 0.2
    실측) 전이 횟수도 job당 2~3번뿐이라 이 트레이드오프가 맞다.

    TTL(`ttl` 속성, 테이블 설정에서 활성화)이 예전 `_JOBS_MAX` 상한 정리를
    대신한다 -- 사이즈 캡이 아니라 시간 기준 정리라 "아직 안 끝난 job이
    상한을 넘겨 밀려나는" M9급 문제 자체가 안 생긴다.

    boto3는 이 클래스가 실제로 인스턴스화될 때만 import한다(메서드 밖,
    `__init__` 안) -- app/engines/analysis/fetch.py:500과
    tests/test_fetch.py:131이 "fetch 경로엔 AWS 자격증명이 없다"를 SSRF
    방어 논거로 명시하는데, `JOB_STORE_BACKEND=memory`(기본값)로 도는 한
    boto3가 로드조차 안 되면 그 방어 논거가 최대한 그대로 유지된다. 이
    저장소 자체는 job_id/idempotencyKey로만 접근하고 사용자가 준 URL을
    그대로 안 쓰므로(fetch.py의 SSRF 표면과 무관) 이 클래스가 생기는 것
    자체는 그 불변식을 깨지 않는다 -- 다만 문구는 "boto3가 전혀 없다"에서
    "fetch 경로엔 boto3가 없다"로 정확하게 좁혀야 한다(app/config.py의
    D-job-store 주석 참고).
    """

    def __init__(self, model_cls: type[T], jobs_table: str, idem_table: str, ttl_days: int = 7):
        import boto3  # 지연 import, 클래스 docstring 참고

        self._model_cls = model_cls
        self._ttl_seconds = ttl_days * 86400
        resource = boto3.resource("dynamodb")
        self._jobs_table = resource.Table(jobs_table)
        self._idem_table = resource.Table(idem_table)

    def _ttl(self) -> int:
        return int(time.time()) + self._ttl_seconds

    def get(self, job_id: str) -> T | None:
        resp = self._jobs_table.get_item(Key={"job_id": job_id})
        item = resp.get("Item")
        if item is None:
            return None
        return self._model_cls.model_validate(json.loads(item["body"]))

    def create(self, job: T) -> None:
        self.save(job)

    def save(self, job: T) -> None:
        body = job.model_dump(mode="json", by_alias=False)
        self._jobs_table.put_item(Item={
            "job_id": job.job_id,
            "body": json.dumps(body, ensure_ascii=False),
            "ttl": self._ttl(),
        })

    def get_id_by_idempotency_key(self, key: str) -> str | None:
        resp = self._idem_table.get_item(Key={"idempotency_key": key})
        item = resp.get("Item")
        return item["job_id"] if item else None

    def set_idempotency_key(self, key: str, job_id: str) -> None:
        self._idem_table.put_item(Item={
            "idempotency_key": key,
            "job_id": job_id,
            "ttl": self._ttl(),
        })

    def delete_idempotency_key(self, key: str) -> None:
        self._idem_table.delete_item(Key={"idempotency_key": key})


_stores: dict[str, "JobStore"] = {}
_stores_lock = threading.Lock()


def get_job_store(kind: str, model_cls: type[T]) -> "JobStore[T]":
    """kind별로 하나씩 만들어 캐싱한다(jobs.py="analysis", curricula.py="curriculum",
    reports.py="report"). 세 kind가 DynamoDB 백엔드에서는 같은 테이블
    (`teamiz-ai-jobs`)을 공유한다 -- job_id가 uuid4라 kind가 달라도 충돌
    걱정이 없고, 어차피 각 모듈은 자기가 만든 job_id만 조회한다.

    설정을 최초 호출 시점에 읽어 굳힌다 -- 프로세스 수명 동안 백엔드가 바뀌는
    시나리오가 없다(배포로만 바뀐다). 테스트가 백엔드를 바꾸고 싶으면 이
    캐시 자체를 monkeypatch하거나(`app.job_store._stores`), 모듈의 `_store`를
    직접 교체한다.
    """
    with _stores_lock:
        if kind not in _stores:
            from app.config import get_settings

            settings = get_settings()
            if settings.job_store_backend == "dynamodb":
                _stores[kind] = DynamoDbJobStore(
                    model_cls,
                    settings.job_store_dynamodb_table,
                    settings.job_store_idempotency_table,
                )
                log.info("job_store: kind=%s backend=dynamodb table=%s",
                         kind, settings.job_store_dynamodb_table)
            else:
                _stores[kind] = InMemoryJobStore(max_items=2000)
        return _stores[kind]
