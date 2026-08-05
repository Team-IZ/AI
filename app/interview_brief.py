""" 면담 브리프 생성 요청 처리 + 멱등 캐시 (jobs.py·sessions.py와 형제).

이 경로는 동기라 job 개념이 없다 -- 그래서 sessions.py의 `_answered`와 같은 이유
(X-Idempotency-Key 재전송 시 LLM을 다시 부르지 않는다)로 여기가 그 캐시의 자리다.
"""

from collections import OrderedDict

from app.engines import interview_brief as engine
from app.engines.interview_brief import InterviewBriefResult
from app.schemas.interview_brief import InterviewBriefRequest

_RESULT_MAX = 500
_results: "OrderedDict[str, InterviewBriefResult]" = OrderedDict()


def generate(req: InterviewBriefRequest, *, idempotency_key: str | None = None) -> InterviewBriefResult:
    """멱등키가 같은 재전송이면 재계산 없이 그대로 돌려준다."""
    if idempotency_key and idempotency_key in _results:
        return _results[idempotency_key]

    result = engine.generate(req)

    if idempotency_key:
        _results[idempotency_key] = result
        while len(_results) > _RESULT_MAX:
            _results.popitem(last=False)
    return result
