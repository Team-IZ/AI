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
# 🔴 QUESTION_GENERATION → CODE_SESSION 개명 확정(2026-08-05, Team-IZ/Backend
# `origin/feat/DangerTrainee` 대조): 이전엔 "CODE_SESSION에만 걸리는 티어 제약"과
# 채팅 표현이 서로 어긋나 보여 ANSWER_EVALUATION 쪽 아닌가 의심했었다(EXIT: 백엔드
# 확인 필요로 보류). 실제로 백엔드 소스를 열어 확인한 결과:
#   - `AiUsage.java` javadoc: "QUESTION_GENERATION·SUMMARY_DRAFT가 CODE_SESSION
#     하나로 통합" (SUMMARY_DRAFT는 여기서 질문생성 파이프라인 내부의 옛 요약
#     서브스텝 명칭이라 /reports의 SUMMARY_DRAFT와는 다른 계보 — REPORT_GENERATION
#     매핑과 충돌하지 않는다. `OrganizationUsageResponse.java`가 최종 확인.)
#   - `OrganizationPolicy.java`: "질문 생성·요약이 CODE_SESSION 기능 하나로
#     통합되면서 티어도 한 값(code_session_tier_code)으로 합쳐졌다" — 의심했던
#     티어 제약이 오히려 이 매핑을 뒷받침한다(합쳐지기 전엔 question_generation_
#     tier_code/summary_tier_code 두 컬럼이었다).
#   - `OrganizationUsageResponse.java`(API 문서, 결정적): "CODE_SESSION(코드 세션
#     질문 생성)" — ANSWER_EVALUATION은 별도로 "(답변 채점)"이라 명시돼 겹치지 않는다.
# 세 소스가 전부 같은 결론이라 engine.py의 5개 stamp도 함께 바꾼다.
FeatureCode = Literal[
    "CODE_ANALYSIS",        # 코드 분석 문서
    "CODE_SESSION",         # 문제·질문·힌트 동결 생성 (v08, 옛 QUESTION_GENERATION)
    "ANSWER_EVALUATION",    # 답변 채점 (세션 중 유일한 호출)
    "CURRICULUM_ANALYSIS",  # 교안 분석
    "REPORT_GENERATION",    # 보고서 서술 (v08, 옛 SUMMARY_DRAFT)
    "INTERVIEW_BRIEF_GENERATION",  # 면담 브리프 (2026-08-07)
]

# 어느 **업무 엔터티**를 처리한 호출인가. featureCode보다 굵은 단위다 — 한 ContextType
# 안에서 featureCode가 여럿 나온다(ANALYSIS_JOB 하나에 CODE_ANALYSIS + CODE_SESSION).
#
# 🔴 옛 값 4개(`ANALYSIS`·`GRADING`·`REPORT`·`CURRICULUM`)는 **전부 CHECK 밖이었다**
# (2026-08-04 대조). 그대로 두면 네 API의 원장 행이 하나도 안 들어간다. 아래는 실제
# CHECK 12종 중 AI가 쓰는 값이다 — 값은 테이블 이름이지 기능 이름이 아니다.
#
# 🔴 `SUBMISSION`은 폐기다(2026-08-07 백엔드 회신, 제안서 D). /analyses는 이제
# `ANALYSIS_JOB` + jobId를 쓴다 — 같은 제출을 재분석하면 실행별 비용이 구분돼야 한다.
# 안 쓰는 값을 남겨두면 누군가 다시 쓰므로 아예 뺀다.
#
# ⚠️ **contextId의 주인이 둘로 갈린다.**
#   ANALYSIS_JOB·ASSESSMENT_SESSION — AI가 아는 값(자기 jobId / 요청의 sessionId)이라
#     그대로 쓰면 된다.
#   REPORT_SNAPSHOT·CURRICULUM_ANALYSIS — **AI는 그 PK를 받은 적이 없다.** 지금은 AI
#     내부 jobId가 들어간다. 저장할 때 Spring이 자기가 아는 PK로 교체해야 한다.
ContextType = Literal[
    "ANALYSIS_JOB",         # POST /analyses               contextId = jobId ✅
    "ASSESSMENT_SESSION",   # POST /sessions/{id}/answers  contextId = sessionId ✅
    "REPORT_SNAPSHOT",      # POST /reports                contextId = 보고서 jobId ⚠️ Spring이 교체
    "CURRICULUM_ANALYSIS",  # POST /curricula              contextId = 교안 jobId ⚠️ Spring이 교체
    "INTERVIEW_BRIEF",      # POST /interview-briefs  contextId = briefId ✅ 요청에서 에코
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
                    "🔴 2026-08-10 합의: `ai_model.model_code`를 "
                    "`provider_model_code`와 **같은 값**(벤더 접두어 포함, 예 "
                    "`nvidia/nemotron-3-ultra-550b-a55b`)으로 등록한다. 그래야 "
                    "`WHERE m.model_code = ?` 조회가 그대로 맞는다",
    )

    # 어느 작업에 딸린 호출인가. 다형 참조라 FK가 없다.
    context_type: ContextType
    context_id: str | None = Field(
        default=None,
        description="처리한 업무 엔터티의 PK. ANALYSIS_JOB이면 jobId, "
                    "ASSESSMENT_SESSION이면 sessionId. ⚠️ REPORT_SNAPSHOT· "
                    "CURRICULUM_ANALYSIS는 AI가 그 PK를 몰라 jobId가 들어간다 — "
                    "Spring이 저장 시점에 실제 PK로 교체해야 한다. "
                    "INTERVIEW_BRIEF는 대신할 값조차 없어 null이다(DB도 이 컬럼만 "
                    "NULL 허용) — 채우려면 요청에 briefId가 필요하다",
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