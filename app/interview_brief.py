""" 면담 브리프 생성 요청 처리 + 멱등 캐시 (jobs.py·sessions.py와 형제).

이 경로는 동기라 job 개념이 없다 -- 그래서 sessions.py의 `_answered`와 같은 이유
(X-Idempotency-Key 재전송 시 LLM을 다시 부르지 않는다)로 여기가 그 캐시의 자리다.
"""

import hashlib
from collections import OrderedDict

from app.engines import interview_brief as engine
from app.engines.interview_brief import InterviewBriefResult
from app.schemas.interview_brief import InterviewBriefRequest

_RESULT_MAX = 500
# 멱등키 -> (요청 지문, 결과). 지문을 같이 들고 있는 이유는 아래 _fingerprint 참고.
_results: "OrderedDict[str, tuple[str, InterviewBriefResult]]" = OrderedDict()


def _fingerprint(req: InterviewBriefRequest) -> str:
    """요청 본문의 지문. 같은 멱등키로 **다른 요청**이 오는 걸 잡는다.

    🔴 지문 없이 키만 보고 캐시를 돌려주면 백엔드가 키를 재사용한 순간 **다른
    교육생의 브리프**가 나간다. develop이 이미 같은 버그를 한 번 고쳤고
    (jobs.py job_id_for_key, redteam audit H12), 백엔드 DDL도 같은 판단을 했다 --
    `interview_brief.last_request_id`와 `last_request_fingerprint`가 CHECK로 묶인
    한 쌍이다(둘 다 있거나 둘 다 없거나).

    jobs.py처럼 식별 필드 몇 개만 대조하지 않고 본문 전체를 해싱하는 이유: 이
    요청에는 "이 브리프가 무엇인가"를 정하는 단일 PK가 없다(brief_id를 안 받는다).
    """
    return hashlib.sha256(
        req.model_dump_json(by_alias=True).encode("utf-8")
    ).hexdigest()


def generate(req: InterviewBriefRequest, *, idempotency_key: str | None = None) -> InterviewBriefResult:
    """멱등키 + 요청 지문이 모두 같은 재전송이면 재계산 없이 그대로 돌려준다."""
    fingerprint = _fingerprint(req)
    if idempotency_key:
        cached = _results.get(idempotency_key)
        if cached is not None:
            cached_fingerprint, result = cached
            if cached_fingerprint != fingerprint:
                raise ValueError(
                    "idempotencyKey가 이전 요청과 다른 본문으로 재사용됐습니다"
                )
            return result

    result = engine.generate(req)

    if idempotency_key:
        _results[idempotency_key] = (fingerprint, result)
        while len(_results) > _RESULT_MAX:
            _results.popitem(last=False)
    return result
