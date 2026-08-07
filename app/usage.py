""" ai_usage 원장 행 만들기. 네 경로(분석·채점·보고서·교안)가 함께 쓴다.

엔진은 job_id도 헤더도 모른다(알 이유도 없다). 반대로 `llm/client.py`는 어느 기능이
불렀는지 모른다 — 그건 엔진이 `feature_code`로 찍어 보낸다. 셋이 나눠 채우는 구조이고
마지막 조각이 여기다.

2026-08-03(PLAN §T11 F1)에 jobs.py에서 올라왔다. 분석만 원장을 채우고 채점·보고서·교안은
빈 배열로 나가고 있었다 — **비용은 Spring이 계산한다**가 계약인데 토큰을 안 보내면
근거가 없다. 특히 채점은 학생 수 × 문제 3 × 축 4라 호출 건수가 가장 많다.
"""

from typing import Any

from pydantic import ValidationError

from app.schemas.usage import AiUsage, ContextType


def to_ai_usage(
    raw_usages: list[dict[str, Any]], context_type: ContextType, context_id: str | None,
    *, feature_code: str | None = None,
    idempotency_key: str | None = None, trace_id: str | None = None,
) -> list[AiUsage]:
    """엔진이 모은 호출 기록에 **요청 범위 값**을 채워 원장 행으로 만든다.

    `feature_code`는 기본값이다 — 엔진이 이미 찍어 보냈으면 그쪽이 이긴다.

    `context_id`는 None일 수 있다(면담 브리프 -- AI가 brief_id를 받은 적이 없다.
    DB도 `ai_usage.context_id`만 NULL 허용이다). 그때는 request_id·trace_id·
    idempotency_key의 폴백이 멱등키/트레이스로 내려간다 -- 그 셋은 DB가 NOT NULL이라
    빈 문자열로 남기면 안 되고, 특히 idempotency_key는 **전역 UNIQUE**라 두 번째
    호출이 곧바로 충돌한다.

    **한 줄이 깨져도 나머지는 보낸다.** 원장은 과금 근거라 "일부라도" 남는 게
    "전부 없음"보다 낫다. 실패한 호출도 토큰을 태웠기 때문이다.
    """
    fallback = context_id or idempotency_key or trace_id or ""
    rows: list[AiUsage] = []
    for i, usage in enumerate(raw_usages, start=1):
        try:
            rows.append(AiUsage.model_validate({
                "feature_code": feature_code,
                "context_type": context_type,
                "context_id": context_id,
                "request_id": fallback,
                "trace_id": trace_id or fallback,
                # 한 작업이 LLM을 여러 번 부른다. 요청 멱등키를 그대로 쓰면 행마다
                # 같은 키가 되고 Spring이 하나로 합쳐 나머지 토큰이 사라진다.
                # 그래서 순번을 붙인다(스키마가 명시한 {contextId}:{contextType}:{attemptNo}).
                "idempotency_key": f"{idempotency_key or fallback}:{context_type}:{i}",
                **usage,
            }))
        except ValidationError:
            continue
    return rows
