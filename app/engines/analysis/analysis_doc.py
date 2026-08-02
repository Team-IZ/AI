""" p04-1 코드 분석 문서를 만든다. buildAnalysisDoc() 포팅.

**파이프라인의 첫 LLM 단계이자 병목이다.** 문제 선정(p04-3)·질문(p04-4)·보고서(p04-6)가
전부 이 문서를 프롬프트 입력으로 받는다. 여기가 비면 뒤가 전부 빈다.

**두 가지 모양을 오간다 — 섞으면 안 된다.**

    build()      LLM 원본 모양 그대로 (decision_points[].file / symbol / related_teach)
                 → 다운스트림 프롬프트에 json.dumps로 그대로 실린다
    to_schema()  API 응답 모양 (source_path / line_start / evidence_valid)
                 → 줄 번호를 여기서 산정한다

원본을 그대로 흘리는 이유: p04-3·p04-6이 이 JSON을 다시 프롬프트에 넣는데, 스키마
모양으로 바꿔 넣으면 모델이 본 적 없는 키를 보게 된다. 변환은 응답 경계에서 한 번만 한다.

**줄 번호는 LLM이 세지 않는다.** 모델은 소스에 실제로 있는 한 줄(symbol)만 문자 그대로
복사하고, 그 문자열을 파일에서 찾아 우리가 산정한다. 못 찾으면 `evidence_valid=False`로
남기고 줄 번호를 비운다 — 지어낸 위치를 근거로 보여주면 "코드 파편이 곧 근거"라는
전제가 무너진다.
"""

from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import fragments, stages

# 프롬프트에 실을 룰 finding 개수 상한. 매니페스트의 findings_block 예산(6000자)이
# 최종 방어선이지만, 잘린 목록보다 온전한 상위 N개가 낫다.
MAX_FINDINGS = 20


@dataclass
class Document:
    """p04-1 결과. `document`가 LLM 원본 모양이다."""

    document: dict[str, Any]
    usages: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)   # 버린 항목의 이유


def _teaches_block(teaches: list[dict[str, Any]]) -> str:
    if not teaches:
        return "(이번 검증에 지정된 teach 없음)"
    return "\n".join(f"- {t.get('id')}: {t.get('label', '')}" for t in teaches)


def _findings_block(candidates: list[dict[str, Any]]) -> str:
    """룰 스캐너 결과를 프롬프트 줄로. 순서는 이미 rank 내림차순이다."""
    if not candidates:
        return "(구조 스캐너가 찾은 finding 없음)"
    lines = []
    for c in candidates[:MAX_FINDINGS]:
        where = c.get("source_path") or "(파일 미상)"
        lines.append(f"- [{c.get('problem_type', '?')}] {where}: {c.get('summary', '')}")
    return "\n".join(lines)


def _clean_areas(raw: Any) -> list[dict[str, Any]]:
    areas = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        files = [f for f in (item.get("files") or []) if isinstance(f, str) and f.strip()]
        area = str(item.get("area") or "").strip()
        if not area:
            continue
        areas.append({"area": area, "files": files, "role": str(item.get("role") or "").strip()})
    return areas


def _clean_points(raw: Any, teach_ids: set[str], dropped: list[str]) -> list[dict[str, Any]]:
    """decision_point에서 쓸 수 있는 것만 남긴다.

    file·symbol이 없으면 줄 번호를 산정할 수 없어 근거로 못 쓴다 — 조용히 통과시키면
    나중에 evidenceValid=false인 문제만 잔뜩 생긴다. 여기서 버리고 이유를 남긴다.
    """
    points = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        source_file = str(item.get("file") or "").strip()
        symbol = str(item.get("symbol") or "").strip()
        if not (title and source_file and symbol):
            dropped.append(f"불완전한 decision_point: {title or '(제목 없음)'}")
            continue

        # 모델이 지어낸 teach id는 버린다. 남겨두면 보고서가 없는 교안을 가리킨다.
        teach = item.get("related_teach")
        related = teach if isinstance(teach, str) and teach in teach_ids else None

        points.append({
            "title": title,
            "file": source_file,
            "symbol": symbol,
            "why_it_matters": str(item.get("why_it_matters") or "").strip(),
            "related_teach": related,
        })
    return points


def build(files: dict[str, str], teaches: list[dict[str, Any]],
          candidates: list[dict[str, Any]], *, model_code: str,
          timeout_s: float | None = None) -> Document:
    """분석 문서를 만든다. 반환값의 `document`는 LLM 원본 모양이다."""
    stage = stages.get_stage("p04-1")
    code_budget = (stage.get("truncation") or {}).get("code_block", 12000)

    values = {
        "teaches_block": _teaches_block(teaches),
        "findings_block": _findings_block(candidates),
        "code_block": fragments.build_code_block(files, max_chars=code_budget),
    }

    result = stages.call("p04-1", values, model_code=model_code, timeout_s=timeout_s)

    data = result.data
    overview = str(data.get("overview") or "").strip()
    if not overview:
        # overview는 AnalysisDocument의 필수 필드다. 비어 있으면 응답 검증에서 어차피
        # 깨지는데, 그때는 뒤 단계 토큰까지 태운 뒤다. 여기서 끊는다.
        raise stages.StageError("p04-1: overview가 비었습니다", result.usages)

    dropped: list[str] = []
    document = {
        "overview": overview,
        "structure": _clean_areas(data.get("structure")),
        "decision_points": _clean_points(
            data.get("decision_points"),
            {t.get("id") for t in teaches if t.get("id")},
            dropped,
        ),
        "risks": [str(r).strip() for r in (data.get("risks") or []) if str(r).strip()],
    }
    return Document(document=document, usages=result.usages, dropped=dropped)


def to_schema(document: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    """API 응답용 `AnalysisDocument` 모양으로. 여기서 줄 번호를 산정한다.

    `build()`가 낸 원본을 받아 변환만 한다 — LLM을 다시 부르지 않는다.
    """
    points = []
    for dp in document.get("decision_points") or []:
        located = fragments.locate_symbol(files, dp.get("file"), dp.get("symbol", ""))
        valid = bool(located.get("valid"))
        points.append({
            "title": dp.get("title", ""),
            # 못 찾아도 모델이 지목한 경로는 남긴다. 사람이 검수할 단서다.
            "source_path": located["file"] if valid else (dp.get("file") or ""),
            "symbol": dp.get("symbol", ""),
            # evidence_valid=False면 줄 번호는 반드시 비어야 한다(DecisionPoint 검증).
            "line_start": located["line_start"] if valid else None,
            "line_end": located["line_end"] if valid else None,
            "why_it_matters": dp.get("why_it_matters", ""),
            "related_teach_id": dp.get("related_teach"),
            "evidence_valid": valid,
        })

    return {
        "overview": document.get("overview", ""),
        "structure": document.get("structure") or [],
        "decision_points": points,
        "risks": document.get("risks") or [],
    }
