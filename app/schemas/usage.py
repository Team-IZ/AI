""" ai_usage 원장 한 줄. 세 API가 공통으로 실어 보낸다.

담당 경계(C-3 확정): AI는 토큰·모델·지연·상태만 보내고 단가·비용은 Spring이 계산한다.
모델을 고르는 주체가 백엔드·프론트라 단가도 그쪽이 먼저 알고, AI에 단가표를 두면
단가가 바뀔 때마다 AI를 재배포해야 한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema

# 어느 기능이 호출했나. **DB ai_usage.feature_code CHECK와 글자까지 같은 집합이다**
# (테이블정의서 v08 기준, 2026-08-05 정렬).
#
# 🔴 옛 `GRADING`은 폐기했다 — v06 CHECK에 없는 값이라 채점 호출의 원장 행이
# 전부 INSERT에서 거부됐을 자리다. 정식 이름은 `ANSWER_EVALUATION`이다.
#
# 🔴 v08(2026-08-05, 면담브리프 API 명세서 §2.4): `SUMMARY_DRAFT`는 CHECK에서
# 제거되고 `REPORT_GENERATION`이 신설됐다 — `SUMMARY_DRAFT`를 그대로 두면 이전의
# GRADING과 같은 사고(INSERT 거부)가 `/reports` 원장 행에서 반복된다. 여기서는
# 반영했다(app/reports.py도 같이 고침).
#
#   WHY: 같은 v08 DDL 주석은 "QUESTION_GENERATION → CODE_SESSION(명칭변경)"이라고도
#   적혀 있다. 하지만 CODE_SESSION에만 걸리는 티어 제약(tier_code/tier_policy_id
#   필수)과 박종호님의 채팅 표현("이해도 검증 세션...CODE_SESSION으로 변경")을 보면
#   오히려 `ANSWER_EVALUATION`(세션 채점 호출) 쪽이 CODE_SESSION이 돼야 하는 것처럼
#   읽힌다 — 문서 간 서술이 어긋난다.
#   COST: 잘못 추측해서 바꾸면 `/analyses`(질문생성)나 `/sessions/answers`(채점) 둘 중
#   하나의 원장 행이 새 CHECK 밖으로 나가 INSERT가 거부된다 — 추측성 수정이 이 값을
#   맞히려다 다른 값을 깨뜨릴 위험이 실재한다. 그래서 QUESTION_GENERATION은 아직
#   그대로 둔다(app/engines/analysis/engine.py의 5곳 stamp 미변경).
#   EXIT: 백엔드에 "QUESTION_GENERATION이 CODE_SESSION이 맞나요, 아니면
#   ANSWER_EVALUATION인가요?" 확인 후 정확한 쪽만 바꾼다.
FeatureCode = Literal[
    "CODE_ANALYSIS",        # 코드 분석 문서
    "QUESTION_GENERATION",  # 문제·질문·힌트 동결 생성 -- CODE_SESSION 개명 대상인지 확인 필요(위 주석)
    "ANSWER_EVALUATION",    # 답변 채점 (세션 중 유일한 호출)
    "CURRICULUM_ANALYSIS",  # 교안 분석
    "REPORT_GENERATION",    # 보고서 서술 (v08, 옛 SUMMARY_DRAFT)
]

# 어느 **업무 엔터티**를 처리한 호출인가. featureCode보다 굵은 단위다 — 한 ContextType
# 안에서 featureCode가 여럿 나온다(SUBMISSION 하나에 CODE_ANALYSIS + QUESTION_GENERATION).
#
# 🔴 옛 값 4개(`ANALYSIS`·`GRADING`·`REPORT`·`CURRICULUM`)는 **전부 v06 CHECK 밖이었다**
# (2026-08-04 대조). 그대로 두면 네 API의 원장 행이 하나도 안 들어간다. 아래는 v06
# CHECK 8종 중 AI가 쓰는 4개다 — 값은 테이블 이름이지 기능 이름이 아니다.
#
# ⚠️ **contextId의 주인이 둘로 갈린다.**
#   SUBMISSION·ASSESSMENT_SESSION — AI가 요청에서 받은 실제 PK를 넣는다. 그대로 쓰면 된다.
#   REPORT_SNAPSHOT·CURRICULUM_ANALYSIS — **AI는 그 PK를 받은 적이 없다.** 지금은 AI
#     내부 jobId가 들어간다. 저장할 때 Spring이 자기가 아는 PK로 교체해야 한다.
ContextType = Literal[
    "SUBMISSION",           # POST /analyses               contextId = submissionId ✅
    "ASSESSMENT_SESSION",   # POST /sessions/{id}/answers  contextId = sessionId ✅
    "REPORT_SNAPSHOT",      # POST /reports                contextId = 보고서 jobId ⚠️ Spring이 교체
    "CURRICULUM_ANALYSIS",  # POST /curricula              contextId = 교안 jobId ⚠️ Spring이 교체
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
    context_id: str = Field(
        description="처리한 업무 엔터티의 PK. SUBMISSION이면 submissionId, "
                    "ASSESSMENT_SESSION이면 sessionId. ⚠️ REPORT_SNAPSHOT· "
                    "CURRICULUM_ANALYSIS는 AI가 그 PK를 몰라 jobId가 들어간다 — "
                    "Spring이 저장 시점에 실제 PK로 교체해야 한다",
    )
    request_id: str
    trace_id: str = Field(description="요청 헤더 X-Trace-Id를 그대로 잇는다")
    idempotency_key: str = Field(
        description="`{요청 단위 키}:{contextType}:{호출 순번}`. "
                    "🔴 DB에서 **전역 UNIQUE**라 요청 단위 키가 요청마다 달라야 한다 — "
                    "세션은 clientRequestId, 분석은 submissionId:attemptNo, "
                    "보고서는 problemId:scoreRunId, 교안은 versionId:analysisVersion이다",
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