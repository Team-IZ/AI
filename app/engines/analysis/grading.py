""" p04-5 답변 하나를 축 하나의 루브릭으로 채점한다.

**힌트 상한은 채점 이후에 시스템이 적용한다.** 모델에게는 "힌트 받았다고 깎지 마라"고
지시하고, 원점수(bestScore)를 그대로 받은 뒤 우리가 상한을 씌워 confirmedScore를 만든다.
그래야 "몇 번째 힌트에서 통과했는가"가 자력의 측정값이 된다 — 모델이 임의로 깎으면
그 측정이 오염된다.

missing/evidence는 버리는 값이 아니다. **적응형 힌트와 L3·L4 질문 생성의 입력**이다
(학생이 정확히 무엇을 빠뜨렸는지를 겨냥해야 하기 때문).
"""

from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import scoring, stages
from app.llm import client


@dataclass
class Grade:
    """한 단계 한 시도의 채점 결과."""

    axis_code: str
    best_score: int              # 루브릭 원점수 0~5. 힌트 상한 적용 전
    confirmed_score: int         # 상한 적용 후. DB problem_stage에 남는 값
    hints_used: int
    passed: bool
    autonomy: str                # SELF · SELF_MAINTAINED · PARTIAL
    matched_level: str           # 이 점수를 준 근거가 된 루브릭 단계 서술
    evidence: str                # 답변에서 그 판단의 근거가 된 부분
    missing: str                 # 한 단계 위를 받으려면 뭐가 더 있어야 했는지
    model_reached: bool | None = None   # 모델이 낸 도달 판정. 없으면 None (vendor P-1)
    reach_conflict: bool = False        # 모델 판정과 점수 기준이 어긋났다
    usages: list[dict[str, Any]] = field(default_factory=list)


def _hints_block(hints: list[str]) -> str:
    if not hints:
        return "(힌트 없이 1차 답변)"
    return "\n".join(f"힌트 {i}: {text}" for i, text in enumerate(hints, start=1))


def grade(axis_code: str, question: str, answer: str, *, model_code: str,
          hints: list[str] | None = None, code_snippet: str = "",
          code_ref: str = "") -> Grade:
    """답변 하나를 채점한다.

    hints는 이 시도 전에 학생이 받은 힌트들이다. 길이가 곧 hintsUsed이고,
    그게 점수 상한(5/4/3)과 자력 판정을 정한다.
    """
    hints = hints or []
    hints_used = len(hints)

    result = stages.call("p04-5", {
        "rubric_block": scoring.rubric_block(axis_code),
        "question": question,
        "hints_used": hints_used,
        "hints_block": _hints_block(hints),
        "code_block": code_snippet or "(근거 코드 없음)",
        "code_ref": code_ref or "-",
        "answer": answer,
    }, model_code=model_code, timeout_s=client.SESSION_TIMEOUT_S,
       max_attempts=client.SESSION_MAX_ATTEMPTS)

    raw = result.data.get("score")
    try:
        best = max(0, min(5, int(raw)))
    except (TypeError, ValueError):
        # 점수가 정수가 아니면 채점 실패다. 0점으로 밀면 학생이 억울하게 깎이므로
        # 예외로 올려 재시도·PARTIAL 판정에 맡긴다.
        raise stages.StageError(f"p04-5: score가 정수가 아닙니다: {raw!r}", result.usages)

    confirmed = min(best, scoring.cap_for(hints_used))
    passed = confirmed >= scoring.PASS_SCORE

    # 모델이 낸 도달 판정(vendor P-1). 점수(척도)와 도달(판정)을 따로 받아 교차 검증한다.
    #
    # **어긋나면 점수를 따른다.** 힌트 상한이 점수에 걸리므로 통과 판정이 점수와 따로 놀면
    # "5점인데 미달" 같은 상태가 생긴다 — 점수가 상한·자력 판정·정렬 tie-break의 근거라
    # 그쪽을 단일 기준으로 둔다. 불일치는 버리지 않고 남긴다: 루브릭 문구와 도달 기준이
    # 서로 다른 말을 하고 있다는 신호이고, 쌓이면 루브릭을 고쳐야 한다는 뜻이다.
    raw_reached = result.data.get("reached")
    model_reached = bool(raw_reached) if isinstance(raw_reached, bool) else None

    return Grade(
        axis_code=axis_code,
        best_score=best,
        confirmed_score=confirmed,
        hints_used=hints_used,
        passed=passed,
        autonomy=scoring.autonomy_for(hints_used),
        model_reached=model_reached,
        reach_conflict=model_reached is not None and model_reached != passed,
        matched_level=str(result.data.get("matched_level") or ""),
        evidence=str(result.data.get("evidence") or ""),
        missing=str(result.data.get("missing") or ""),
        usages=result.usages,
    )