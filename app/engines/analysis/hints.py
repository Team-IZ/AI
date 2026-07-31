""" p04-7 힌트(= 재질의 문장) 하나를 만든다. hint-ladder.js의 generateHint() 포팅.

**한 함수가 두 모드를 모두 처리한다.** 매니페스트가 `{attempts_block}`으로 갈라준다.

    동결(L1·L2)   attempts=[] 로 부른다. 분석 배치에서 답변 없이 미리 만든다
    적응형(L3·L4)  실제 시도 기록으로 부른다. 오답 확정 직후 세션 중에 만든다

두 모드가 같은 강도 spec(scoring.HINT_LADDER)을 공유한다 — 비교 가능성의 근거가
"힌트 텍스트가 같다"가 아니라 "사다리 강도·횟수·점수 상한이 같다"인 이유다.

**힌트 미생성은 구조적으로 막는다.** 규칙 위반이나 빈 응답이 재시도 후에도 계속되면
결정론적 폴백 문장으로 대체한다. `generated=False`로 남겨 감사할 수 있게 한다 —
DB `stage_answer_attempt`의 `attempt_no IN (2,3) AND hint_text IS NOT NULL` CHECK가
항상 만족되려면 힌트가 반드시 나와야 한다.
"""

from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import guard, scoring, stages
from app.llm import client


@dataclass
class Hint:
    hint_level: int
    kind: str                # "관점 되짚기" · "범위 좁힘"
    text: str
    generated: bool          # False면 폴백 문장이 쓰였다는 뜻
    usages: list[dict[str, Any]] = field(default_factory=list)


def format_attempts(question: str, attempts: list[dict[str, Any]]) -> str:
    """지금까지의 시도 전문. 적응형 힌트가 겨냥할 대상이 여기서 나온다.

    채점기의 missing/evidence까지 넣는 것이 핵심이다 — 학생이 정확히 무엇을
    빠뜨렸는지를 알아야 힌트가 그 지점을 겨냥한다. 점수만 주면 "더 자세히
    설명해보세요" 같은 무의미한 힌트가 나온다.
    """
    if not attempts:
        return f"질문: {question}\n(아직 답변 없음)"

    blocks = []
    for i, a in enumerate(attempts, start=1):
        hint_note = f" (힌트 {i - 1}: {a['hint']})" if a.get("hint") else ""
        blocks.append(
            f"시도 {i}{hint_note}:\n"
            f"  질문: {a.get('question') or question}\n"
            f"  답변: {a.get('answer', '')}\n"
            f"  채점: {a.get('confirmed_score', '?')}점 -- "
            f"missing: {a.get('missing') or '(없음)'} / evidence: {a.get('evidence') or '(없음)'}"
        )
    return "\n\n".join(blocks)


def fallback(hint_level: int, code_ref: str) -> str:
    """규칙 위반·빈 응답이 계속될 때 쓰는 결정론적 문장.

    사다리 강도는 지킨다 — 1은 관점 되짚기, 2는 범위 좁힘. 정답도 선택지도 주지 않는다.
    """
    if int(hint_level) == 1:
        return (f"방금 답변에서 다루지 않은 부분이 있습니다. {code_ref}을 다시 살펴보고, "
                "이전 답변에 빠진 관점이 무엇인지 스스로 점검해보세요.")
    return (f"질문 범위를 좁혀 다시 묻습니다. {code_ref}에서 가장 핵심적인 한 부분만 골라, "
            "그 부분만 설명해보세요.")


def generate(hint_level: int, question: str, *, model_code: str,
             attempts: list[dict[str, Any]] | None = None,
             teach: dict[str, Any] | None = None,
             code_snippet: str = "", code_ref: str = "",
             timeout_s: float | None = None) -> Hint:
    """힌트 하나를 만든다. attempts가 비어 있으면 동결 사전 생성이다."""
    spec = scoring.HINT_LADDER[int(hint_level)]
    stage = stages.get_stage("p04-7")
    max_regenerations = {p["key"]: p["default"] for p in stage.get("params", [])}.get(
        "max_regenerations", 1
    )
    ref = code_ref or "-"

    teach_intent = (
        f"이 힌트가 학생을 다시 이끌어야 할 개념: {teach.get('label', '')}\n"
        f"(참고용 요약 -- 문장을 그대로 인용하지 말 것: {teach.get('summary') or '요약 없음'})"
        if teach else "(연결된 teach 없음 -- 코드와 질문만 근거로 힌트를 만들 것)"
    )

    values = {
        "hint_level": f"{hint_level} ({spec['kind']})",
        "hint_strength_spec": spec["spec"],
        "question": question,
        "attempts_block": format_attempts(question, attempts or []),
        "teach_intent_block": teach_intent,
        "code_block": code_snippet or "(근거 코드 없음)",
        "code_ref": ref,
    }

    usages: list[dict[str, Any]] = []
    for attempt in range(max_regenerations + 1):
        try:
            result = stages.call("p04-7", values, model_code=model_code,
                                 timeout_s=timeout_s or client.SESSION_TIMEOUT_S)
        except stages.StageError as exc:
            usages.extend(exc.usages)
            continue

        usages.extend(result.usages)
        text = str(result.data.get("hint") or "").strip()
        # 힌트에도 선택지 금지가 걸린다. 힌트에 보기가 섞이면 사다리 최강 단계를
        # 공짜로 주는 셈이라 자력/보조 구분이 무너진다.
        if text and not guard.check(text):
            return Hint(hint_level=hint_level, kind=spec["kind"], text=text,
                        generated=True, usages=usages)

    return Hint(hint_level=hint_level, kind=spec["kind"], text=fallback(hint_level, ref),
                generated=False, usages=usages)


def freeze_for_stage(question: str, *, model_code: str, teach: dict[str, Any] | None = None,
                     code_snippet: str = "", code_ref: str = "") -> list[Hint]:
    """동결 단계(L1·L2)의 힌트 2개를 답변 없이 미리 만든다.

    분석 배치에서만 부른다. 문제 3개 × 단계 2개 × 힌트 2개 = 12콜이 여기서 나온다.
    서로 독립이라 병렬화 대상이다(T7c).
    """
    return [
        generate(level, question, model_code=model_code, attempts=[], teach=teach,
                 code_snippet=code_snippet, code_ref=code_ref,
                 timeout_s=client.DEFAULT_TIMEOUT_S)   # 배치라 넉넉히
        for level in (1, 2)
    ]
