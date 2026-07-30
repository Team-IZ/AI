""" LLM 호출 1건의 latency_ms를 재는 헬퍼

D-timing (2026-07-30): 실제 LLM 호출을 감싸는 지점을 이 클래스 하나로 통일한다.
  WHY: 엔진마다 각자 시간을 재면(모노토닉 vs 벽시계, 반올림 방식 등) 나중에
    항목별로 latencyMs를 더하거나 비교할 때 기준이 어긋난다. with 블록 하나로
    통일하면 모든 엔진의 latencyMs가 같은 방식으로 잰 값이 된다.
  COST: 실제 LLM 호출부(아직 엔진이 스텁이라 없음)를 짤 때 이 클래스를 빼먹고
    직접 time.time()을 쓰면 이 통일성이 깨진다 -- 강제할 방법은 코드 리뷰뿐.
  EXIT: 나중에 tracing(OpenTelemetry 등)으로 옮기고 싶어지면, 이 클래스의
    build() 반환 타입(AiUsage)만 유지한 채 내부 구현만 교체하면 된다.

D-usage-realign (2026-07-31): develop에 팀원이 실제 app.schemas.usage.AiUsage를
이미 만들어 랜딩했다(app/schemas/usage.py, 커밋 d1c8b84) -- 이 파일이 그때까지
쓰던 자체 제작 AiUsageEntry(app/schemas/common.py)는 지우고 진짜 스키마를 그대로
쓴다. 그 스키마는 request_id/trace_id를 요구하는데(README 문서에서 예측 못 했던
필드), trace_id는 아직 API 레이어가 X-Trace-Id 헤더를 실제로 꿰어주지 않으므로
(app/api/analyses.py 주석: "아직 사용 X") source_id로 대체하는 자리표시자를 쓴다 --
헤더 배선이 실제로 붙으면 이 기본값을 걷어내고 호출부에서 명시적으로 넘기면 된다.

idempotencyKey 조립은 README.md "aiUsage" 절의 계약 그대로: "{sourceId}:{sourceType}:{attemptNo}".
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from app.schemas.usage import AiUsage


class LlmCallTimer:
    """ with 블록으로 실제 LLM 호출 1건을 감싸고, 끝나면 build()로 AiUsage를 만든다

    사용 예:
        timer = LlmCallTimer(
            feature_code="CODE_ANALYSIS", model_code="glm-5.2",
            source_type="ANALYSIS_DOC", source_id=job_id, attempt_no=1,
        )
        with timer:
            response = call_llm(...)
        entry = timer.build(input_token_count=.., output_token_count=.., status="SUCCEEDED")
    """

    def __init__(
        self,
        feature_code: str,
        model_code: str,
        *,
        source_type: str,
        source_id: str,
        attempt_no: int = 1,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.feature_code = feature_code
        self.model_code = model_code
        self.source_type = source_type
        self.source_id = source_id
        self.attempt_no = attempt_no
        # request_id는 호출 1건의 고유 식별자 -- 안 주면 매 호출마다 새로 만든다.
        self.request_id = request_id or str(uuid.uuid4())
        # trace_id: X-Trace-Id 헤더가 아직 API 레이어에서 안 꿰어져(위 docstring 참고)
        # source_id를 자리표시자로 쓴다 -- 최소한 같은 작업의 여러 호출을 묶어 볼 수 있다.
        self.trace_id = trace_id or source_id
        self._start_monotonic: float = 0.0
        self._occurred_at: datetime | None = None
        self.latency_ms: int = 0

    def __enter__(self) -> "LlmCallTimer":
        self._start_monotonic = time.monotonic()
        self._occurred_at = datetime.now(timezone.utc)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # 예외가 나도(실패한 호출도) latency_ms는 확정한다 -- 실패도 시간은 걸렸다.
        self.latency_ms = round((time.monotonic() - self._start_monotonic) * 1000)

    @property
    def idempotency_key(self) -> str:
        return f"{self.source_id}:{self.source_type}:{self.attempt_no}"

    def build(
        self,
        *,
        input_token_count: int = 0,
        output_token_count: int = 0,
        cached_token_count: int = 0,
        status: str = "SUCCEEDED",
        failure_code: str | None = None,
    ) -> AiUsage:
        """ with 블록이 끝난 뒤(latency_ms가 확정된 뒤) 호출해서 실제 기록을 만든다 """
        occurred_at = self._occurred_at or datetime.now(timezone.utc)
        return AiUsage(
            idempotency_key=self.idempotency_key,
            source_type=self.source_type,
            source_id=self.source_id,
            request_id=self.request_id,
            trace_id=self.trace_id,
            feature_code=self.feature_code,
            model_code=self.model_code,
            input_token_count=input_token_count,
            output_token_count=output_token_count,
            cached_token_count=cached_token_count,
            status=status,
            failure_code=failure_code,
            latency_ms=self.latency_ms,
            occurred_at=occurred_at,
        )
