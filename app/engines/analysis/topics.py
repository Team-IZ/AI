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
    verified = []
    for t in kept:
        ref = t.get("code_ref") or {}
        located = fragments.extract_fragment(files, ref.get("file"), ref.get("symbol", ""))
        if not located["valid"]:
            dropped.append({"title": t.get("title", ""), "reason": located["reason"]})
            continue
        t["code_ref"] = {
            "file": located["file"],
            "line_start": located["line_start"],
            "line_end": located["line_end"],
            "snippet": located["snippet"],
        }
        verified.append(t)

    # 폴백: teach 앵커로 예산을 못 채웠으면 teach 없는 일반 문제로 채운다.
    #
    # 같은 teach를 재사용하지 않는 이유: 강사가 (클래스, 상속, 캡슐화)로 정하면
    # 프론트도 그 셋을 보여준다. 뒤에서 (클래스, 클래스, 캡슐화)로 바꾸면 화면과 어긋난다.
    #
    # 미구현 teach는 이미 requirementResults가 F로 보고한다. 그걸 점수 분모에도
    # 반영하면(문제 2개 = 만점 40) 같은 사실을 두 번 벌하고, 학생마다 만점이 달라진다.
    #
    # LLM을 다시 부르지 않는다 — p04-1의 decision_points가 이미 코드에 앵커돼 있다.
    if len(verified) < question_budget:
        used = {(t["code_ref"]["file"], t["code_ref"]["line_start"]) for t in verified}
        verified.extend(_general_topics(
            files, analysis_document, need=question_budget - len(verified), used=used
        ))

    return Selection(topics=verified[:question_budget], usages=result.usages,
                     dropped=dropped, budget=question_budget)
    
def _general_topics(files: dict[str, str], analysis_document: dict[str, Any],
                    *, need: int, used: set[tuple[str, int]]) -> list[dict[str, Any]]:
    """teach 없는 일반 문제. 분석 문서의 decision_points 중 안 쓰인 것을 쓴다.

    decision_points는 p04-1이 "판단이 개입된 지점"으로 이미 골라둔 것이고 코드에
    앵커돼 있다. 새로 LLM을 부를 이유가 없다.

    teach_id가 None이라 **보고서의 교안 복습 위치 지목이 이 문제엔 안 붙는다.**
    임시안이고, 미구현 teach를 어떻게 다룰지는 나중에 다시 본다.
    """
    picked: list[dict[str, Any]] = []
    for dp in analysis_document.get("decision_points") or []:
        if len(picked) >= need:
            break
        located = fragments.extract_fragment(files, dp.get("file"), dp.get("symbol", ""))
        if not located["valid"]:
            continue
        key = (located["file"], located["line_start"])
        if key in used:
            continue   # 1차에서 이미 쓴 지점. 같은 문제를 두 번 내지 않는다
        used.add(key)
        picked.append({
            "teach_id": None,
            # 조립기가 Problem.is_general로 옮긴다. 화면에 "일반 문제"로 표기해야 한다
            # (2026-08-02 PM 확정) — teach 앵커가 없어 다른 문제와 성격이 다르다.
            "is_general": True,
            "title": dp.get("title", ""),
            "rationale": dp.get("why_it_matters", ""),
            "code_ref": {
                "file": located["file"],
                "line_start": located["line_start"],
                "line_end": located["line_end"],
                "snippet": located["snippet"],
            },
        })
    return picked