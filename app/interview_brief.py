""" 면담 브리프 생성 요청 처리 + 멱등 캐시 (jobs.py·sessions.py와 형제).

이 경로는 동기라 job 개념이 없다 -- 그래서 sessions.py의 `_answered`와 같은 이유
(X-Idempotency-Key 재전송 시 LLM을 다시 부르지 않는다)로 여기가 그 캐시의 자리다.
"""

import hashlib
from collections import OrderedDict

from app.config import get_settings
from app.engines import interview_brief as engine
from app.engines.interview_brief import InterviewBriefItemResult, InterviewBriefResult
from app.schemas.interview_brief import InterviewBriefRequest
from app.usage import stub_usage

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


def _stub_result(req: InterviewBriefRequest) -> InterviewBriefResult:
    """LLM 없이 계약 모양만 만든다(`engine_mode="stub"`, 2026-08-12 신설).

    그전까지 이 경로는 engine_mode를 아예 안 봐서 스텁 배포에서도 실제 LLM을 불렀다 --
    백엔드가 계약을 왕복해 보려는데 무료 티어 529에 막혔다. 스텁 자리는 reports.py·
    curricula.py와 같은 서비스 계층이다(엔진은 순수하게 둔다).

    🔴 **`interviewSourceId`를 지어내지 않는다.** `interview_brief_item.
    interview_source_id`가 UUID NOT NULL이고 요청에 없는 값이면 백엔드가 저장을
    거부한다 -- 실엔진이 모델 출력을 검증하는 이유와 같다. 요청에 실제로 온 값만
    쓴다(`_collect_allowed_source_ids`). 정렬은 결정성을 위해서다(집합엔 순서가 없다).
    """
    ids = sorted(engine._collect_allowed_source_ids(req))
    # 첫 면담이면 6~8개, 아니면 4~8개(§5, engine이 강제하는 하한과 같은 규칙).
    count = 6 if req.brief_context.is_first_interview else 4
    return InterviewBriefResult(
        opening_remark=f"[stub] {req.target.user_name}님, 오늘 잠깐 이야기 나누겠습니다. "
                       f"편하게 답해 주시면 됩니다.",
        items=[
            InterviewBriefItemResult(
                question_text=f"[stub] {order}번 확인 질문입니다. 어떻게 진행하셨나요?",
                question_rationale="[stub] 실제 근거 서술은 엔진 이식 후 생성됩니다.",
                # 1부터 중복 없는 연속 정수.
                suggested_order=order,
                interview_source_id=ids[(order - 1) % len(ids)],
            )
            for order in range(1, count + 1)
        ],
        # 원장 1행. 빈 배열이면 백엔드가 ai_usage 저장 경로를 한 번도 안 밟는다.
        # featureCode는 라우터가 INTERVIEW_BRIEF_GENERATION으로 넘긴다.
        usages=[stub_usage(get_settings().model_code_interview_brief,
                           input_tokens=1200, output_tokens=260, latency_ms=18)],
    )


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

    result = _stub_result(req) if get_settings().engine_mode == "stub" else engine.generate(req)

    if idempotency_key:
        _results[idempotency_key] = (fingerprint, result)
        while len(_results) > _RESULT_MAX:
            _results.popitem(last=False)
    return result
