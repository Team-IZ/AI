""" p04-4 문제 하나의 L1~L4 질문 4개를 답변 보기 전에 동결한다.
hint-ladder.js의 freezeQuestionSet() 포팅 (질문 부분만 — 힌트는 p04-7).

**질문·힌트 전부 분석 배치에서 동결된다**(2026-08-02 최종). 세션 중 LLM 호출은
채점 하나뿐이다.

생성물을 믿지 않는다. 두 가지를 검사하고 어기면 재생성한다.
  ① 형태 — levels가 정확히 4개이고 축이 L1→L4 순서인가
  ② 선택지 — 질문에 보기가 섞이지 않았는가 (guard.py)

재생성 상한을 넘으면 **flagged로 남긴다. 조용히 통과시키지 않는다** — 선택지가 섞인
질문 하나가 그 문제의 자력 측정을 통째로 무효화하기 때문이다.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
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

    `wanted`에 없는 축은 버린다. 전면 동결(2026-08-02)에서는 `wanted`가 L1~L4 전부라
    실제로 버려지는 것이 없다 — 매니페스트 p04-4가 원래 4개를 만든다.
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
           *, model_code: str, fallback_model_code: str | None = None,
           axes: tuple[str, ...] = scoring.FROZEN_AXES,
           timeout_s: float | None = None, max_attempts: int | None = None) -> QuestionSet:
    """문제 하나의 질문 L1~L4를 답변 보기 전에 동결한다 (2026-08-02 전면 동결).

    `axes` 기본값이 `scoring.FROZEN_AXES` = 4축 전부다. 세션 중 질문 생성은 없다.
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
                                 fallback_model_code=fallback_model_code,
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

        # 학생이 보는 텍스트다. 백틱 인용이 코드 중간에서 끊긴 채로 나가면 안 된다.
        for level in levels:
            level["question"] = fragments.repair_code_quotes(level["question"], code_block)

        violations = guard.check_levels(
            [{"axis": lv["axis_code"], "question": lv["question"]} for lv in levels]
        )
        if not violations:
            return QuestionSet(topic=topic, levels=levels, code_ref=ref, usages=usages)

        reason = "선택지 위반: " + ", ".join(f"{v['axis']}/{v['matched']}" for v in violations)

    # 상한을 넘었다. 조용히 통과시키지 않는다 — 사람이 봐야 한다.
    return QuestionSet(topic=topic, levels=[], code_ref=ref, usages=usages,
                       flagged=True, reason=reason)


# 동시 호출 상한. hints.py의 MAX_PARALLEL과 같은 값·같은 이유(키 8개 여유는 크지만
# 무한정 던지면 공급자 큐 혼잡에 우리가 기여한다).
MAX_PARALLEL = 8


def freeze_many(topics: list[dict[str, Any]], files: dict[str, str],
                teach_map: dict[str, Any], *, model_code: str,
                fallback_model_code: str | None = None,
                max_workers: int = MAX_PARALLEL) -> list[QuestionSet]:
    """여러 문제의 질문 L1~L4를 **동시에** 동결한다. 반환 순서는 `topics` 순서와 같다.

    # D-parallel-questions(2026-08-21): engine.py의 p04-4 루프를 순차 호출에서
    #   병렬 호출로 바꾼다(hints.freeze_many()와 같은 패턴).
    #   WHY: 문제(topic)끼리는 서로의 결과를 참조하지 않는 완전한 독립 호출인데
    #        engine.py가 문제 수만큼 순차로 freeze()를 돌렸다. NVIDIA 대형 모델은
    #        공급자 쪽 큐 혼잡 시 단일 호출이 300초+ 걸린 전례가 있어(nvidia_client.py
    #        의 D98 — llama-3.3-70b-instruct 워커큐 과부하 실측) 순차 3콜이면 최악의
    #        경우 15분을 넘는다. 실측(2026-08-21, teamiz-prod 최근 성공 job 20건,
    #        question_budget 전부 3으로 동일)에서 처리시간이 44초~1289초까지 30배
    #        벌어졌다 — hints.py가 2026-08-02에 정확히 같은 근거(힌트 8콜=전체
    #        902초의 68%)로 이미 병렬화된 전례를 그대로 따른다.
    #   COST: 문제 수만큼 스레드가 동시에 NVIDIA 키 풀에 접근한다. MAX_PARALLEL=8은
    #        hints.py와 동일 상한이라 두 배치가 겹쳐도(질문 배치 다음에 힌트 배치가
    #        순차로 이어지므로 실제로는 안 겹침) 새로 늘어나는 동시 접속은 없다.
    #   EXIT: freeze()가 내부에서 예외를 흡수하지 않고 밖으로 던지도록 바뀌면,
    #        future.result()가 그 예외를 그대로 재던지므로 이 함수에 try/except를
    #        추가해야 한다(현재는 hints.generate()와 마찬가지로 freeze()가 항상
    #        QuestionSet을 반환하므로 불필요).
    """
    if not topics:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(freeze, topic, files, teach_map.get(topic.get("teach_id")),
                       model_code=model_code, fallback_model_code=fallback_model_code): i
            for i, topic in enumerate(topics)
        }
        out: list[QuestionSet | None] = [None] * len(topics)
        for future in as_completed(futures):
            out[futures[future]] = future.result()

    return out  # type: ignore[return-value]