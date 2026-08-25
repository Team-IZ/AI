""" DynamoDbJobStore 검증. moto로 실제 AWS 없이 DynamoDB API를 흉내낸다.

핵심 검증 대상은 ECS Fargate 마이그레이션 Phase 2의 합격 기준 그 자체다:
**태스크 A가 만든 job을 태스크 B에서 GET하면 200이어야 한다.** InMemoryJobStore는
프로세스 하나 안에서만 도니 이 시나리오를 재현할 수 없다 -- 그래서 서로 다른
DynamoDbJobStore 인스턴스 두 개(같은 테이블을 가리키는, 서로 다른 ECS 태스크를
흉내낸) 사이의 교차 조회를 직접 확인한다(test_write_from_one_instance_is_visible_
from_another).
"""

from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from app.job_store import DynamoDbJobStore
from app.schemas.analysis import AnalysisJobStatus

JOBS_TABLE = "test-teamiz-ai-jobs"
IDEM_TABLE = "test-teamiz-ai-idem"


@pytest.fixture
def dynamodb_tables():
    """moto로 잡은 가짜 DynamoDB에 실제 테이블 구조(PK만, GSI 없음)를 만든다."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ap-northeast-1")
        client.create_table(
            TableName=JOBS_TABLE,
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.create_table(
            TableName=IDEM_TABLE,
            KeySchema=[{"AttributeName": "idempotency_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "idempotency_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def _job(job_id: str, status: str = "QUEUED") -> AnalysisJobStatus:
    return AnalysisJobStatus(job_id=job_id, status=status)


def test_get_missing_job_returns_none(dynamodb_tables):
    store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE)
    assert store.get("no-such-job") is None


def test_create_then_get_round_trips(dynamodb_tables):
    store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE)
    job = _job("job-1")
    store.create(job)

    fetched = store.get("job-1")
    assert fetched is not None
    assert fetched.job_id == "job-1"
    assert fetched.status == "QUEUED"
    # DynamoDbJobStore는 매번 새로 역직렬화한다 -- InMemoryJobStore와 달리
    # 원본과 같은 객체가 아니다(job_store.py의 InMemoryJobStore docstring 참고).
    assert fetched is not job


def test_save_overwrites_full_item(dynamodb_tables):
    """save()는 전체 덮어쓰기다(부분 UpdateItem이 아니다) -- 상태 전이가
    다음 get()에 그대로 보이는지 확인한다."""
    store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE)
    job = _job("job-2")
    store.create(job)

    job.status = "RUNNING"
    job.started_at = datetime(2026, 8, 26, 3, 0, 0, tzinfo=timezone.utc)
    store.save(job)

    fetched = store.get("job-2")
    assert fetched.status == "RUNNING"
    assert fetched.started_at == job.started_at

    job.status = "SUCCEEDED"
    job.completed_at = datetime(2026, 8, 26, 3, 1, 0, tzinfo=timezone.utc)
    store.save(job)

    fetched = store.get("job-2")
    assert fetched.status == "SUCCEEDED"
    assert fetched.started_at == job.started_at  # 이전 필드도 안 사라진다(전체 재작성이라)
    assert fetched.completed_at == job.completed_at


def test_write_from_one_instance_is_visible_from_another(dynamodb_tables):
    """Phase 2 합격 기준: 서로 다른 태스크(=서로 다른 DynamoDbJobStore 인스턴스)가
    같은 job을 주고받을 수 있어야 한다."""
    task_a_store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE)
    task_b_store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE)

    job = _job("cross-task-job")
    task_a_store.create(job)  # "태스크 A"가 만듦

    fetched_by_b = task_b_store.get("cross-task-job")  # "태스크 B"가 폴링
    assert fetched_by_b is not None
    assert fetched_by_b.job_id == "cross-task-job"
    assert fetched_by_b.status == "QUEUED"

    # 태스크 B가 상태를 바꾸고, 태스크 A가 다시 읽어도 보여야 한다(양방향).
    fetched_by_b.status = "RUNNING"
    task_b_store.save(fetched_by_b)
    assert task_a_store.get("cross-task-job").status == "RUNNING"


def test_idempotency_key_round_trip(dynamodb_tables):
    store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE)
    assert store.get_id_by_idempotency_key("key-1") is None

    store.set_idempotency_key("key-1", "job-42")
    assert store.get_id_by_idempotency_key("key-1") == "job-42"

    store.delete_idempotency_key("key-1")
    assert store.get_id_by_idempotency_key("key-1") is None


def test_delete_missing_idempotency_key_does_not_raise(dynamodb_tables):
    """존재하지 않는 키를 지워도(예: 이미 TTL로 정리된 경우) 예외가 나면 안 된다 --
    jobs.py의 job_id_for_key()가 '처음 보는 키'로 취급하는 정리 경로에서 부른다."""
    store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE)
    store.delete_idempotency_key("never-existed")


def test_ttl_attribute_is_set_on_write(dynamodb_tables):
    """DynamoDB TTL(백엔드가 이걸로 자동 정리한다, _JOBS_MAX 상한을 대신함)이
    실제로 매 쓰기마다 실리는지 -- moto는 TTL을 실시간으로 만료시키진 않지만,
    속성 자체가 있는지는 확인할 수 있다."""
    store = DynamoDbJobStore(AnalysisJobStatus, JOBS_TABLE, IDEM_TABLE, ttl_days=7)
    store.create(_job("ttl-job"))

    raw = boto3.resource("dynamodb", region_name="ap-northeast-1").Table(JOBS_TABLE) \
        .get_item(Key={"job_id": "ttl-job"})["Item"]
    assert "ttl" in raw
    assert raw["ttl"] > 0
