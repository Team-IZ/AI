""" p04-3 문제 선정 + 그 뒤의 검증 2단계. poc-engine.js:104~137 포팅.

LLM이 고른 topic을 그대로 믿지 않는다. 두 가지를 확인하고 어긴 것은 버린다.
  ① teach_id가 실제로 준 teaches 안에 있고 서로 다른가
  ② code_ref의 symbol이 실제 파일에서 위치를 잡히는가

②를 여기서 안 걸러내면 질문·힌트 생성이 "근거 없음"인 채로 계속 돌아
LLM 호출만 태우고 세션에서 조용히 깨진다(PoC가 실제로 겪은 경로).
"""

import json
from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import fragments, stages


@dataclass
class Selection:
    topics: list[dict[str, Any]]          # 검증 통과분. code_ref에 산정된 줄 번호가 들어 있다
    usages: list[dict[str, Any]]
    dropped: list[dict[str, str]] = field(default_factory=list)   # [{title, reason}]
    # 문항을 못 만든 teach. **`problems`에 없는 teachId를 백엔드가 역산하지 않도록**
    # 명시적으로 들고 나온다 — 화면의 `―`(문항 없음)이 이 값으로 그려진다.
    unmatched: list[dict[str, str]] = field(default_factory=list)  # [{teach_id, reason}]
    budget: int = 3

    @property
    def shortfall(self) -> int:
        """요청 개수에 못 미친 수. 0이면 정상.

        0이 아니어도 실패가 아니다 — 억지로 채우면 물을 거리가 없는 문제가 섞인다.
        Spring에는 questionCountPlanned와 problems 길이 차이로 드러난다.
        """
        return self.budget - len(self.topics)


def select(files: dict[str, str], teaches: list[dict[str, Any]],
           analysis_document: dict[str, Any], candidates: list[dict[str, Any]],
           *, model_code: str, question_budget: int = 3) -> Selection:
    """문제 후보를 골라 검증까지 마친 목록을 돌려준다.

    한 topic이 한 teach를 독점하므로 **teaches가 question_budget보다 적으면 문제도
    그만큼만 나온다.** 교안 분석이 teach를 적게 뽑으면 문제 수가 조용히 줄어든다.

    룰 후보(candidates)는 선택지가 아니라 맥락이다 — 매니페스트가 code_ref.file을
    "분석 문서에 등장한 파일"로 제약하고, 환각 방지는 아래 symbol 검증이 담당한다.
    """
    stage = stages.get_stage("p04-3")
    values = {
        "teaches_block": "\n".join(f"- {t.get('id')}: {t.get('label', '')}" for t in teaches),
        "analysis_block": json.dumps(analysis_document, ensure_ascii=False),
        "findings_block": json.dumps(candidates, ensure_ascii=False),
        "question_count": question_budget,
    }
    result = stages.call("p04-3", values, model_code=model_code)

    raw = result.data.get("topics")
    topics = raw if isinstance(raw, list) else []
    dropped: list[dict[str, str]] = []

    # 검증 ①: 존재하는 teach여야 하고 서로 달라야 한다.
    # 없는 teach를 참조하는 문제는 만들 수 없고, 같은 teach를 두 번 물으면 검증 축이 겹친다.
    teach_ids = {t.get("id") for t in teaches}
    seen: set[str] = set()
    kept = []
    for t in topics:
        tid = t.get("teach_id")
        if tid not in teach_ids:
            dropped.append({"title": t.get("title", ""), "reason": f"없는 teach: {tid}"})
            continue
        if tid in seen:
            dropped.append({"title": t.get("title", ""), "reason": f"teach 중복: {tid}"})
            continue
        seen.add(tid)
        kept.append(t)

    # 검증 ②: symbol이 실제 파일에서 잡혀야 한다. 산정된 줄 번호를 code_ref에 되먹여
    # 이후 단계가 symbol을 다시 찾지 않고 "산정된 사실"만 쓰게 한다.
    verified, failed = _locate_all(files, kept, dropped)

    # 🔴 **재시도 1회** (2026-08-03 PM 결정: "일단 최대한 teaches에 부합하는 거 찾아보고
    # 그래도 없으면 없다고 박아라"). 개념이 코드에 **있는데 LLM이 엉뚱한 symbol을 지목**한
    # 경우가 있다 — 한 번에 버리면 있는 개념을 없다고 박게 된다.
    #
    # 실패한 teach만 모아 다시 묻는다. 전부 성공하면 이 호출은 아예 없다.
    if failed:
        retried = _relocate(files, teaches, analysis_document, candidates, failed,
                            model_code=model_code)
        if retried is not None:
            result.usages.extend(retried.usages)
            more, _ = _locate_all(files, retried.topics, dropped)
            verified.extend(more)

    picked = verified[:question_budget]

    # 요청받은 teach 중 문항이 안 나온 것. 지어내지 않고 "없음"으로 남긴다
    # (2026-08-03 PM 결정). 사유는 화면에 그대로 띄울 수 있는 한 문장으로 만든다.
    matched = {t.get("teach_id") for t in picked}
    reason_by_teach = {d.get("teach_id"): d.get("reason") for d in dropped if d.get("teach_id")}
    unmatched = [
        {"teach_id": t["id"],
         "reason": reason_by_teach.get(t["id"])
                   or "제출 코드에서 이 개념의 근거를 찾지 못했습니다"}
        for t in teaches[:question_budget] if t.get("id") and t["id"] not in matched
    ]

    return Selection(topics=picked, usages=result.usages, dropped=dropped,
                     unmatched=unmatched, budget=question_budget)
    
def _locate_all(files: dict[str, str], topics: list[dict[str, Any]],
                dropped: list[dict[str, str]]) -> tuple[list[dict[str, Any]],
                                                        list[dict[str, Any]]]:
    """symbol을 실제 파일에서 잡아 code_ref에 줄 번호를 되먹인다.

    돌려주는 것은 (검증 통과, 위치를 못 잡은 것). 못 잡은 것도 `dropped`에 사유가
    남는다 — 재시도가 실패하면 그 사유가 최종 기록이다.
    """
    verified: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for topic in topics:
        ref = topic.get("code_ref") or {}
        located = fragments.extract_fragment(files, ref.get("file"), ref.get("symbol", ""))
        if not located["valid"]:
            dropped.append({"title": topic.get("title", ""), "reason": located["reason"],
                            "teach_id": topic.get("teach_id") or ""})
            failed.append(topic)
            continue
        topic["code_ref"] = {
            "file": located["file"],
            "line_start": located["line_start"],
            "line_end": located["line_end"],
            "snippet": located["snippet"],
        }
        verified.append(topic)
    return verified, failed


def _relocate(files: dict[str, str], teaches: list[dict[str, Any]],
              analysis_document: dict[str, Any], candidates: list[dict[str, Any]],
              failed: list[dict[str, Any]], *, model_code: str):
    """위치를 못 잡은 teach만 모아 p04-3을 한 번 더 부른다.

    **개념이 코드에 있는데 LLM이 엉뚱한 symbol을 지목한 경우를 구제한다.**
    한 번에 버리면 있는 개념을 "없음"으로 박게 되고, 그건 오퍼레이터가 고른 개념을
    조용히 빼는 것이다(2026-08-03 PM: "최대한 찾아보고 그래도 없으면 없다고 박아라").

    **한 번만 한다.** 두 번째도 못 찾으면 실제로 코드에 없을 가능성이 훨씬 높고,
    LLM 콜을 더 태울 값어치가 없다. 실패하면 조용히 None을 돌려준다 — 재시도가
    깨져서 1차 결과까지 잃으면 안 된다.
    """
    failed_ids = {t.get("teach_id") for t in failed}
    subset = [t for t in teaches if t.get("id") in failed_ids]
    if not subset:
        return None

    values = {
        "teaches_block": "\n".join(
            f"- {t.get('id')}: {t.get('label', '')}" for t in subset),
        "analysis_block": json.dumps(analysis_document, ensure_ascii=False),
        "findings_block": json.dumps(candidates, ensure_ascii=False),
        "question_count": len(subset),
    }
    missed = "\n".join(
        f"- {t.get('teach_id')}: {(t.get('code_ref') or {}).get('symbol', '')!r}"
        for t in failed
    )
    hint = (
        "\n\n## 재시도\n"
        "앞선 시도에서 아래 symbol을 코드에서 찾지 못했다. **파일에 실제로 존재하는 "
        "선언·호출 문자열을 그대로** code_ref.symbol에 써라 — 요약하거나 다시 쓰지 마라.\n"
        f"{missed}\n\n"
        "해당 개념이 코드에 실제로 없으면 그 teach는 topics에서 빼라. "
        "지어내지 마라 — 없는 것은 없다고 두는 편이 낫다."
    )
    try:
        result = stages.call("p04-3", values, model_code=model_code, extra_user=hint)
    except stages.StageError:
        return None

    raw = result.data.get("topics")
    topics = raw if isinstance(raw, list) else []
    # 재시도가 엉뚱한 teach를 들고 오면 무시한다. 1차에서 이미 성공한 것을 덮어쓰면 안 된다.
    topics = [t for t in topics if t.get("teach_id") in failed_ids]
    return Selection(topics=topics, usages=result.usages, budget=len(subset))
