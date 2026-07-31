""" p04-4 문제 하나의 L1~L4 질문 4개를 답변 보기 전에 동결한다.
hint-ladder.js의 freezeQuestionSet() 포팅 (질문 부분만 — 힌트는 p04-7).

**질문은 어느 힌트 모드에서든 분석 배치에서 동결된다.** 세션 시작에 AI 호출이 없는
근거가 이것이고, 동결/적응형이 갈리는 것은 힌트뿐이다.

생성물을 믿지 않는다. 두 가지를 검사하고 어기면 재생성한다.
  ① 형태 — levels가 정확히 4개이고 축이 L1→L4 순서인가
  ② 선택지 — 질문에 보기가 섞이지 않았는가 (guard.py)

재생성 상한을 넘으면 **flagged로 남긴다. 조용히 통과시키지 않는다** — 선택지가 섞인
질문 하나가 그 문제의 자력 측정을 통째로 무효화하기 때문이다.
"""

from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import fragments, guard, scoring, stages


@dataclass
class QuestionSet:
    """문제 하나의 동결된 질문 4개."""

    topic: dict[str, Any]
    levels: list[dict[str, str]]          # [{axis_code, question}] L1~L4 순서
    code_ref: dict[str, Any]
    usages: list[dict[str, Any]] = field(default_factory=list)
    flagged: bool = False
    reason: str | None = None             # flagged일 때 왜 막혔는지


def _topic_block(topic: dict[str, Any]) -> str:
    ref = topic.get("code_ref") or {}
    lines = [f"제목: {topic.get('title', '')}"]
    if topic.get("rationale"):
        lines.append(f"선정 이유: {topic['rationale']}")
    lines.append(f"근거 위치: {fragments.format_ref(ref.get('file', '?'), ref.get('line_start'), ref.get('line_end'))}")
    return "\n".join(lines)


def _teach_block(teach: dict[str, Any] | None) -> str:
    if not teach:
        return "(연결된 teach 없음)"
    pages = teach.get("source_pages") or teach.get("unit_pages") or []
    page_text = f"p.{', '.join(str(p) for p in pages)}" if pages else "페이지 미상"
    return (f"Unit {teach.get('unit_id', '?')} · {teach.get('label', '')} ({page_text})\n"
            f"{teach.get('summary', '')}")


def _normalize(raw_levels: Any, wanted: tuple[str, ...]) -> list[dict[str, str]] | None:
    """모델 출력에서 `wanted` 축의 질문만 뽑아 순서대로. 하나라도 없으면 None.

    모델은 PoC 축 ID(L1_코드기술)로 답한다 — 여기서 우리 계약 코드(L1)로 바꾼다.
    순서가 틀려도 축 코드로 다시 세우므로, 모델이 순서를 흔들어도 결과는 항상 진행 순서다.

    **남는 축은 버린다.** 매니페스트 p04-4는 아직 L1~L4를 한 번에 만든다. 혼합 모드로
    바뀌면서 분석 배치에서 동결할 것은 L1·L2뿐이라, 팀원이 프롬프트를 고치기 전까지는
    4개를 받아 2개만 쓴다. 고쳐지면 2개만 오고 이 함수는 그대로 동작한다.
    """
    if not isinstance(raw_levels, list):
        return None

    by_code: dict[str, str] = {}
    for level in raw_levels:
        if not isinstance(level, dict):
            continue
        code = scoring.POC_ID_TO_CODE.get(level.get("axis"))
        question = level.get("question")
        if code is None or not isinstance(question, str) or not question.strip():
            continue
        by_code[code] = question.strip()

    if not set(wanted).issubset(by_code):
        return None
    return [{"axis_code": code, "question": by_code[code]} for code in wanted]


def freeze(topic: dict[str, Any], files: dict[str, str], teach: dict[str, Any] | None,
           *, model_code: str, axes: tuple[str, ...] = scoring.FROZEN_AXES,
           timeout_s: float | None = None, max_attempts: int | None = None) -> QuestionSet:
    """문제 하나의 동결 대상 질문을 만든다.

    기본은 L1·L2다(2026-07-31 PM 확정 혼합 모드). L3·L4는 세션 중에 직전 답변을
    근거로 만들므로 여기서 만들지 않는다.

    **세션 중에 부를 때는 timeout_s를 반드시 넘겨야 한다.** 안 넘기면 배치 상한(600초)이
    적용돼 학생이 그만큼 기다린다 — 실측에서 이 경로가 910초를 태웠다.
    """
    stage = stages.get_stage("p04-4")
    max_regenerations = {p["key"]: p["default"] for p in stage.get("params", [])}.get(
        "max_regenerations", 2
    )

    ref = topic.get("code_ref") or {}
    code_block = ref.get("snippet") or "(근거 코드 파편을 확인할 수 없음)"
    code_ref_str = fragments.format_ref(ref.get("file", "?"), ref.get("line_start"), ref.get("line_end"))

    values = {
        "topic_block": _topic_block(topic),
        "code_block": code_block,
        "code_ref": code_ref_str,
        "teach_block": _teach_block(teach),
        "axis_intent_block": scoring.axis_intent_block(),
    }

    usages: list[dict[str, Any]] = []
    reason: str | None = None

    for attempt in range(max_regenerations + 1):
        try:
            result = stages.call("p04-4", values, model_code=model_code,
                                 timeout_s=timeout_s,
                                 **({"max_attempts": max_attempts} if max_attempts else {}))
        except stages.StageError as exc:
            usages.extend(exc.usages)
            reason = f"호출 실패: {exc}"
            continue

        usages.extend(result.usages)
        levels = _normalize(result.data.get("levels"), axes)
        if levels is None:
            got = result.data.get("levels")
            reason = (f"형태 불일치 — {list(axes)} 축이 다 오지 않았습니다 "
                      f"(levels {len(got) if isinstance(got, list) else '없음'}개)")
            continue

        violations = guard.check_levels(
            [{"axis": lv["axis_code"], "question": lv["question"]} for lv in levels]
        )
        if not violations:
            return QuestionSet(topic=topic, levels=levels, code_ref=ref, usages=usages)

        reason = "선택지 위반: " + ", ".join(f"{v['axis']}/{v['matched']}" for v in violations)

    # 상한을 넘었다. 조용히 통과시키지 않는다 — 사람이 봐야 한다.
    return QuestionSet(topic=topic, levels=[], code_ref=ref, usages=usages,
                       flagged=True, reason=reason)