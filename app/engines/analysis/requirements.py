""" p04-2 요구사항 P/F 판정. Requirements.judge() 포팅.

강사가 정한 요구사항 각각에 대해 제출 코드가 충족하는지 P/F를 낸다. 문답과는 별개
경로다 — 이 결과는 문제 선정에 쓰이지 않고 `AnalysisResult.requirement_results`로
바로 나간다.

**결과는 요청 requirements와 1:1이어야 한다.** `jobs.py:60`이 개수 일치를 검사하고,
개수가 어긋나면 분석 전체가 실패한다. 그래서 모델이 몇 개를 주든 우리는 항상 요청
개수만큼 만들어 낸다 — 모자란 자리는 `F` + note로 채운다.

**매칭은 텍스트 우선, 위치는 폴백이다.** 프롬프트가 "순서와 개수를 일치시키라"고
지시하지만 그걸 믿고 인덱스로만 붙이면, 모델이 하나를 빠뜨렸을 때 그 뒤가 통째로
한 칸씩 밀린다 — **에러 없이 다른 요구사항의 판정이 붙는다.** 학생이 통과한 항목이
F가 되는 종류의 사고라 조용히 넘길 수 없다.
"""

from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import fragments, stages

# 근거를 못 찾았을 때의 판정. 프롬프트 규칙과 같다 — 추정으로 P를 주지 않는다.
# 🔴 DB `project_requirement_assessment.result` CHECK가 PENDING/PASS/FAIL이다
# (테이블정의서 v06). 옛 축약값 'P'/'F'를 보내면 Spring이 매번 두 글자를 풀어야 했다.
_PASS = "PASS"
_FAIL = "FAIL"


@dataclass
class Judgement:
    """p04-2 결과. `results`는 항상 요청 requirements와 같은 길이·순서다."""

    results: list[dict[str, Any]]
    usages: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)   # 모델 응답에서 못 찾은 요구사항 id


def _requirements_block(requirements: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{i}. {r.get('text', '')}" for i, r in enumerate(requirements, start=1)
    )


def _norm(text: Any) -> str:
    """비교용 정규화. 모델이 공백·따옴표를 흔들어도 같은 요구사항으로 본다."""
    return " ".join(str(text or "").split()).strip().lower()


def _format_evidence(raw: Any) -> str | None:
    """{file, lines, quote} → "app/pay.py:12-20 — quote" 한 줄로.

    스키마의 evidence가 문자열이라 평탄화한다. 위치와 인용을 둘 다 남기는 이유:
    위치만 있으면 코드가 바뀐 뒤 무엇을 봤는지 알 수 없고, 인용만 있으면 어디인지
    못 찾는다.
    """
    if isinstance(raw, str):
        return raw.strip() or None
    if not isinstance(raw, dict):
        return None

    source_file = str(raw.get("file") or "").strip()
    quote = " ".join(str(raw.get("quote") or "").split()).strip()
    lines = raw.get("lines")
    start = end = None
    if isinstance(lines, list) and lines:
        start = lines[0] if isinstance(lines[0], int) else None
        end = lines[-1] if isinstance(lines[-1], int) else start

    where = fragments.format_ref(source_file, start, end) if source_file else ""
    if where and quote:
        return f"{where} — {quote}"
    return where or quote or None


def _index_results(raw: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """모델 결과를 (요구사항 텍스트 → 결과) 맵과 원래 순서 목록으로."""
    ordered: list[dict[str, Any]] = []
    by_text: dict[str, dict[str, Any]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        ordered.append(item)
        key = _norm(item.get("requirement"))
        if key and key not in by_text:
            by_text[key] = item
    return by_text, ordered


def _verdict(item: dict[str, Any] | None) -> str:
    """모델이 낸 `P`만 통과다. `PASS`·`partial` 같은 값은 전부 FAIL로 본다.

    프롬프트가 `P`/`F` 한 글자를 요구하므로 다른 값이 오면 모델이 형식을 어긴 것이고,
    그때의 판정은 믿을 근거가 없다. **바깥으로 나가는 값만 PASS/FAIL로 쓴다**
    (DB `project_requirement_assessment.result` CHECK) — 모델 어휘와 계약 어휘는 다르다.
    """
    return _PASS if str((item or {}).get("verdict", "")).strip() == "P" else _FAIL


def judge(requirements: list[dict[str, Any]], files: dict[str, str], *,
          model_code: str, timeout_s: float | None = None) -> Judgement:
    """요구사항 전체를 한 번의 호출로 판정한다.

    요구사항이 없으면 호출하지 않는다 — 빈 목록에 토큰을 태울 이유가 없다.
    """
    if not requirements:
        return Judgement(results=[])

    stage = stages.get_stage("p04-2")
    code_budget = (stage.get("truncation") or {}).get("code_block", 12000)

    result = stages.call("p04-2", {
        "requirements_block": _requirements_block(requirements),
        "code_block": fragments.build_code_block(files, max_chars=code_budget),
    }, model_code=model_code, timeout_s=timeout_s)

    by_text, ordered = _index_results(result.data.get("results"))

    results: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for i, req in enumerate(requirements):
        req_id = str(req.get("requirement_id") or req.get("requirementId") or f"req-{i + 1}")

        item = by_text.get(_norm(req.get("text")))
        if item is None:
            # 텍스트로 못 찾았다. 같은 자리의 결과가 **요구사항 텍스트를 아예 안 달고
            # 있을 때만** 폴백으로 쓴다. 다른 요구사항 텍스트를 달고 있다면 목록이
            # 밀린 것이므로 쓰면 안 된다 — 그게 오판정의 원인이다.
            candidate = ordered[i] if i < len(ordered) else None
            if candidate is not None and not _norm(candidate.get("requirement")):
                item = candidate

        if item is None:
            unmatched.append(req_id)
            results.append({
                "requirement_id": req_id,
                "verdict": _FAIL,
                "evidence": None,
                "note": "모델 응답에서 이 요구사항의 판정을 찾지 못했습니다",
            })
            continue

        note = str(item.get("note") or "").strip() or None
        verdict = _verdict(item)
        evidence_raw = item.get("evidence")
        # H4-dev (redteam audit, 2026-08-05): decision_points(analysis_doc.py)/topics(topics.py)
        # 둘 다 fragments.locate_symbol로 실제 소스와 대조하는데 이 판정만 모델의 evidence를
        # 무검증으로 채택했다 -- 제출 코드에 가짜 근거를 심어 P를 유도하는 프롬프트 인젝션의
        # 최종 착지점이 여기였다. P는 evidence.quote가 evidence.file에 실제로 있을 때만
        # 살아남는다; 못 찾으면(지어낸 코드거나 file이 틀렸으면) F로 강등한다. F는 애초에
        # 근거가 필요 없으므로 이 검사를 거치지 않는다.
        if verdict == _PASS:
            ev = evidence_raw if isinstance(evidence_raw, dict) else {}
            located = fragments.locate_symbol(files, ev.get("file"), str(ev.get("quote") or ""))
            if not located.get("valid"):
                verdict = _FAIL
                reason = located.get("reason", "")
                note = f"근거 코드를 확인할 수 없어 F로 강등({reason})" + (f" -- {note}" if note else "")
            else:
                # 모델이 스스로 센 lines가 아니라 실제로 산정된 위치로 교체한다(fragments.py의
                # "산정된 사실 vs LLM의 주장" 분리 원칙 -- analysis_doc.py/topics.py와 동일).
                evidence_raw = {**ev, "file": located["file"], "lines": [located["line_start"], located["line_end"]]}

        results.append({
            "requirement_id": req_id,
            "verdict": verdict,
            "evidence": _format_evidence(evidence_raw),
            "note": note,
        })

    return Judgement(results=results, usages=result.usages, unmatched=unmatched)
