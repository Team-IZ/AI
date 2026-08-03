""" ai_usage 원장 한 줄. 세 API가 공통으로 실어 보낸다.

담당 경계(C-3 확정): AI는 토큰·모델·지연·상태만 보내고 단가·비용은 Spring이 계산한다.
모델을 고르는 주체가 백엔드·프론트라 단가도 그쪽이 먼저 알고, AI에 단가표를 두면
단가가 바뀔 때마다 AI를 재배포해야 한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema

# 어느 기능이 호출했나. DB ai_usage.feature_code CHECK와 같은 집합.
# SESSION_DIALOG는 CHECK에 남아 있지만 쓰지 않는다 — 세션 중 LLM 호출은 채점뿐이다.
FeatureCode = Literal[
    "CODE_ANALYSIS",        # 코드 분석 문서
    "QUESTION_GENERATION",  # 문제·질문·힌트 동결 생성
    "GRADING",              # 답변 채점 (세션 중 유일한 호출)
    "CURRICULUM_ANALYSIS",  # 교안 분석
    "SUMMARY_DRAFT",        # 보고서 서술
]

# 어느 작업에 딸린 호출인가. featureCode보다 굵은 단위다 — 한 ContextType 안에서
# featureCode가 여럿 나올 수 있다(ANALYSIS 하나에 CODE_ANALYSIS + QUESTION_GENERATION).
#
# 🔴 필드명이 `source_type`/`source_id` → `context_type`/`context_id`로 바뀌었다
# (2026-08-03, 백엔드 확인). **값 집합은 아직 확정이 아니다** — 새 MEAS 비고가
# `context_type=ANALYSIS_JOB`·`context_type=PROBLEM_STAGE`처럼 **테이블 이름**을 쓰는데,
# 우리 값은 동사(ANALYSIS·GRADING·…)다. 아래를 확인받기 전까지 값은 그대로 둔다.
#
#   ⚠️ `PROBLEM_STAGE`로 가면 `context_id`가 `problem_stage_id`여야 하는데
#      **AI는 그 값을 모른다.** 세션 요청에 오는 것은 sessionId·problemId·axisCode뿐이다.
#      백엔드가 요청에 실어 보내거나, 채점 컨텍스트를 세션 단위로 두어야 한다.
ContextType = Literal[
    "ANALYSIS",     # POST /analyses                 contextId = 분석 jobId
    "GRADING",      # POST /sessions/{id}/answers    contextId = sessionId
    "REPORT",       # POST /reports                  contextId = 보고서 jobId
    "CURRICULUM",   # POST /curricula                contextId = 교안 jobId
]

# 옛 이름. 다른 모듈이 아직 import할 수 있어 남겨둔다.
SourceType = ContextType

# 기술적 실패 유형. status가 FAILED·PARTIAL일 때만 채운다.
FailureCode = Literal[
    "TIMEOUT",
    "RATE_LIMITED",
    "PROVIDER_ERROR",
    "INVALID_JSON",       # 모델이 JSON 계약을 어김
    "CONTEXT_OVERFLOW",
]


class AiUsage(BaseSchema):
    """LLM 호출 한 번의 기록. DB ai_usage 한 행에 대응한다."""

    feature_code: FeatureCode
    model_code: str = Field(
        description="Spring이 ai_model에서 model_id를 조회한다. "
                    "⚠️ **AI는 호출에 쓴 provider 문자열을 그대로 에코한다**(요청의 "
                    "providerModelCode 또는 서버 기본값) — AI는 화면 선택값을 모른다. "
                    "Spring은 `provider_model_code`로 ai_model을 조회해야 한다",
    )

    # 어느 작업에 딸린 호출인가. 다형 참조라 FK가 없다.
    context_type: ContextType
    context_id: str = Field(description="작업 PK. 분석이면 분석 jobId")
    request_id: str
    trace_id: str = Field(description="요청 헤더 X-Trace-Id를 그대로 잇는다")
    idempotency_key: str = Field(
        description="{contextId}:{contextType}:{attemptNo} 형식"
    )

    input_token_count: int = Field(ge=0)
    output_token_count: int = Field(ge=0)
    cached_token_count: int = Field(default=0, ge=0)

    status: Literal["SUCCEEDED", "FAILED", "PARTIAL"]
    failure_code: FailureCode | None = None
    latency_ms: int = Field(ge=0)
    occurred_at: datetime

    @model_validator(mode="after")
    def _check_db_constraints(self) -> "AiUsage":
        """DB CHECK를 여기서 먼저 건다. 통과 못 하면 Spring INSERT가 깨진다."""
        if self.status == "SUCCEEDED" and self.failure_code is not None:
            raise ValueError("status=SUCCEEDED에는 failureCode가 없어야 합니다")
        if self.status in ("FAILED", "PARTIAL") and self.failure_code is None:
            raise ValueError(f"status={self.status}에는 failureCode가 필요합니다")
        if self.cached_token_count > self.input_token_count:
            raise ValueError("cachedTokenCount는 inputTokenCount를 넘을 수 없습니다")
        return self