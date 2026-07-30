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
    model_code: str = Field(description="Spring이 ai_model에서 model_id를 조회한다")

    # 어느 작업에 딸린 호출인가. 다형 참조라 FK가 없다.
    source_type: str = Field(description="값 목록은 백엔드 확정 대기(C-4)")
    source_id: str = Field(description="작업 PK. 분석이면 analysisId")
    request_id: str
    trace_id: str = Field(description="요청 헤더 X-Trace-Id를 그대로 잇는다")
    idempotency_key: str = Field(
        description="{sourceId}:{sourceType}:{attemptNo} 형식"
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