""" 무거운 배치성 job(코드분석·교안분석)이 함께 쓰는 전역 CPU 예산.

D4 continued(2026-08-25, jobs.py/config.py의 D4 주석과 같은 결정):
코드분석과 교안분석은 job 하나당 ThreadPoolExecutor(max_workers=8)을 똑같이 쓴다
(둘 다 hints.MAX_PARALLEL을 그대로 재사용) -- CPU 부담 프로파일이 같다.

이 세마포어를 jobs.py/curricula.py가 **하나만 공유**하는 이유: 각자 따로
Semaphore(6)을 들면, 최악의 경우 코드분석 6개 + 교안분석 6개 = 12개가 동시에
돌아 2026-08-25 인시던트(job 12개 동시 시작 → CPU 100% 고정, 헬스체크 15초
무응답)를 그대로 재현한다. 하나의 풀을 공유해야 "무거운 job 전체"가 6개를
넘지 않는다는 보장이 실제로 선다.
"""

import threading

from app.config import get_settings

# analysis_max_concurrent_jobs라는 이름은 코드분석 인시던트에서 나왔지만,
# 이 세마포어는 그 이름이 가리키는 것보다 넓게(교안분석까지) 적용된다 -- 필드명을
# 다시 붙이는 비용보다, 여기 이 주석으로 실제 범위를 명확히 하는 쪽을 택했다.
HEAVY_JOB_CONCURRENCY = threading.Semaphore(get_settings().analysis_max_concurrent_jobs)
