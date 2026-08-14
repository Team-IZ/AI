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

    🔴 **`interviewSourceId`를 지어내지 않는다.** 요청에 없는 값이면 백엔드가 저장을
    거부한다 -- 실엔진이 모델 출력을 검증하는 이유와 같다.

    개수·순서는 `engine._compose()`를 그대로 쓰고, **id도 카테고리에 맞는 종류로
    붙인다**(위험 질문엔 위험 사유 id, 문답 질문엔 그 단계 id). 허용 집합에서 아무거나
    골라 쓰면 검증은 통과하지만 백엔드가 화면을 짜면서 "위험 질문인데 왜 문제 단위
    근거지?"를 보게 된다 -- 스텁의 존재 이유가 진짜 모양을 보여주는 거라 그러면 안 된다.

    라포·일반 질문은 설계상 근거가 없어 null이다(백엔드가 `source_type='MANUAL'`로
    저장한다) -- 스텁도 그 경로를 실제로 태워야 백엔드가 null을 만나본다. 라포는
    관찰 메모가 있으면 그 id를 쓴다(실엔진 프롬프트가 그걸 우선하라고 지시한다).

    규칙을 여기 복제하면 실경로와 스텁이 갈린다 -- 실제로 2026-08-12에 "4~8개(첫 면담
    6~8)"가 5-카테고리 고정 구성으로 바뀌면서 한 번 갈렸다.
    """
    composition, qna_targets = engine._compose(req)
    # 카테고리별로 "이 종류의 근거"를 순서대로 꺼내 쓴다. 모자라면 실엔진과 같은
    # 앵커로 메운다(engine._anchor_source_id) -- stub이 null을 내면 계약이 갈린다.
    pools: dict[str, list[str]] = {
        "RAPPORT": [n.interview_source_id for n in req.observation_notes],
        "PRIOR_INTERVIEW": [],   # 이전 상담 내역엔 interviewSourceId가 없다
        "RISK": [r.source_interview_source_id for r in req.risk_reasons],
        "GENERAL": [],           # 일반 질문은 특정 근거가 없다
        "QNA": [s.interview_source_id for _, s in qna_targets],
    }
    used = dict.fromkeys(pools, 0)

    def _next_id(qtype: str) -> str:
        pool, i = pools[qtype], used[qtype]
        used[qtype] = i + 1
        return pool[i] if i < len(pool) else engine._anchor_source_id(req, qtype)

    return InterviewBriefResult(
        opening_remark=f"[stub] {req.target.user_name}님, 오늘 잠깐 이야기 나누겠습니다. "
                       f"편하게 답해 주시면 됩니다.",
        items=[
            InterviewBriefItemResult(
                question_text=f"[stub] {order}번 확인 질문입니다. 어떻게 진행하셨나요?",
                question_rationale="[stub] 실제 근거 서술은 엔진 이식 후 생성됩니다.",
                # 1부터 중복 없는 연속 정수.
                suggested_order=order,
                interview_source_id=_next_id(qtype),
                question_type=qtype,
            )
            for order, qtype in enumerate(composition.sequence(), start=1)
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
