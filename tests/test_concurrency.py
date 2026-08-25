""" jobs.py(코드분석)와 curricula.py(교안분석)가 HEAVY_JOB_CONCURRENCY를
실제로 공유하는지. app/concurrency.py의 D4 주석이 전제하는 속성이다.

2026-08-25 인시던트 회귀 -- 두 job 타입이 세마포어를 따로 들면 "코드분석 6개 +
교안분석 6개 동시"로 같은 CPU 100% 사고가 재현될 수 있다. 여기서는 상한을 3으로
낮춰 코드분석 job과 교안분석 job을 섞어 동시에 던지고, 합쳐서 3을 넘지 않는지만
검증한다(개별 로직은 test_jobs.py/test_curricula.py가 이미 커버한다).
"""
import threading
import time
from typing import Any

from app import curricula as curricula_module
from app import jobs as jobs_module
from app.schemas.analysis import AnalysisRequest
from app.schemas.curriculum import CurriculumRequest

ANALYSIS_BODY = AnalysisRequest.model_validate(
    {
        "method": "GITHUB_URL",
        "source": {"repoUrl": "https://github.com/owner/repo"},
        "extractionScope": "TOTAL",
        "questionBudget": 4,
        "teaches": [{"id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed", "label": "제네릭 타입 경계"}],
    }
)
CURRICULUM_BODY = CurriculumRequest.model_validate({"version_id": "ver-1", "course_label": "Java"})


def test_analysis_and_curriculum_jobs_share_one_cpu_budget(monkeypatch):
    shared = threading.Semaphore(3)
    monkeypatch.setattr(jobs_module, "HEAVY_JOB_CONCURRENCY", shared)
    monkeypatch.setattr(curricula_module, "HEAVY_JOB_CONCURRENCY", shared)

    lock = threading.Lock()
    current = 0
    max_seen = 0

    def _slow(*_args, **_kwargs) -> None:
        nonlocal current, max_seen
        with lock:
            current += 1
            max_seen = max(max_seen, current)
        time.sleep(0.05)
        with lock:
            current -= 1

    monkeypatch.setattr(jobs_module, "_run_analysis_locked", _slow)
    monkeypatch.setattr(curricula_module, "_run_curriculum_locked", _slow)

    analysis_jobs = [jobs_module.create_job(ANALYSIS_BODY, idempotency_key=None) for _ in range(3)]
    curriculum_jobs = [curricula_module.create_job(CURRICULUM_BODY, idempotency_key=None) for _ in range(3)]

    threads = [
        threading.Thread(target=jobs_module.run_analysis, args=(j.job_id, ANALYSIS_BODY, object(), None))
        for j in analysis_jobs
    ] + [
        threading.Thread(target=curricula_module.run_curriculum, args=(j.job_id, CURRICULUM_BODY, None))
        for j in curriculum_jobs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # 6개(코드분석 3 + 교안분석 3)를 던졌지만, 공유 상한(3)을 절대 못 넘는다.
    assert max_seen == 3
