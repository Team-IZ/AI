""" 코드 분석 문서 생성(p05-3) -- poc_full의 p04-1과 필드 단위로 동일한 계약

D1 (2026-07-31): app.engines.shared.llm.chat()을 직접 호출한다(CrewAI 아님).
  WHY: 단일 JSON in/out 호출이라 도구 루프가 필요 없다 -- p05-1(Tier 2 재랭킹)은
    사용자가 명시적으로 CrewAI를 요구했지만 이 스테이지는 그런 요구가 없었고,
    poc_full의 p04-1 원본도 도구 없는 단일 호출이다.
  COST: 같은 파이프라인 안에서 호출 방식이 갈린다(p05-1=crew.py, p05-3=llm.chat 직접).
  EXIT: 나중에 파일 재조회 등 도구가 필요해지면 crew.py 패턴으로 옮긴다.

D2 (사용자 결정, 2026-07-31): decision_points -> problems는 구조화 JSON 그대로 간다
  (마크다운 변환 없음). analysis_document_markdown 필드만 별도로 render_markdown()이
  결정론적으로(LLM 호출 없이) 조립한다 -- 이 필드 하나만 Spring 텍스트 컬럼에
  대응하는 "사람이 읽는 문서"이기 때문이다.

D4: decision_points의 file/related_teach는 ground.py(Tier 2)와 같은 원칙으로
  closed-vocabulary 검증한다 -- file은 반드시 이 스테이지에 실제로 보여준 후보
  경로 안에 있어야 하고, related_teach는 요청이 준 teaches 후보 id이거나 null이어야
  한다. 모델이 지어낸 값은 결과에 나타나지 않는다(rejected에도 고정 사유 코드만 남고
  원문 값은 담지 않는다 -- ground.py의 "자유 서술 절대 미노출" 원칙과 동일).

D5: symbol -> 실제 줄 번호는 app.engines.shared.evidence.locate_symbol()이 찾는다
  (D-poc10: LLM은 줄을 세지 않는다). 못 찾으면 그 decision_point는 problem으로
  만들지 않는다(근거 없는 문제를 낼 수 없음) -- ungrounded 목록에 사유를 남긴다.

D6: 이 스테이지의 입력 코드는 codemap의 Tier1/2가 이미 선정한 파일들이다 --
  poc_full처럼 별도 JS 구조 스캐너(findings_block)가 없다. codemap의 랭킹이 이미
  "무엇이 중요한 코드인가"를 걸렀으므로 그 역할을 대신한다.
  COST: poc_full의 Tier B(auth/eval/secret 키워드) 위험 트리거 스캔이 없다.
  EXIT: 필요해지면 별도 결정론적 위험 스캔 블록을 추가할 수 있다(LLM 호출 없이).

D8: related_teach는 검증까지는 하지만(closed-vocabulary), 현재 Problem 스키마에는
  이 값을 담을 필드가 없다(question_focus_item_id는 강사 지정 focus_items용으로
  별개 개념). 이 모듈은 검증된 related_teach를 DecisionPoint에 보존하지만,
  build_problems()가 만드는 Problem dict에는 반영하지 않는다 -- 스키마에 없는
  필드를 임의로 만들어 넣지 않는다(그건 Backend/Frontend와 합의할 계약 변경이지
  AI 서비스가 단독으로 정할 일이 아니다). 필요해지면 Problem 스키마에 필드를
  추가하는 백엔드 논의가 먼저다.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Mapping, Sequence

from app.engines.codemap.models import AnalysisDoc, DecisionPoint, StructureArea
from app.engines.shared.budget import CallBudget
from app.engines.shared.evidence import evidence_hash, locate_symbol, slice_snippet
from app.engines.shared.llm import ChatResult, chat, classify_failure_code, extract_json_object
from app.engines.shared.prompts import load_stage, param_default, render
from app.engines.shared.timing import LlmCallTimer
from app.schemas.usage import AiUsage

_AXIS_CODES = ("L1", "L2", "L3", "L4")
_QUESTION_GEN_PLACEHOLDER = (
    "코드 중요도 선별 단계 -- 이 파일이 분석 대상으로 선택된 이유는 확인됐지만, "
    "실제 질문은 아직 생성되지 않았습니다(question-generation 스테이지 대기, D8/D10 참고)."
)

EMPTY_DOC = AnalysisDoc(overview="", structure=(), decision_points=(), risks=())


def parse_analysis_doc_response(
    raw: Mapping[str, Any],
    allowed_paths: frozenset[str],
    allowed_teach_ids: frozenset[str],
) -> tuple[AnalysisDoc, tuple[str, ...]]:
    """ p05-3 LLM 응답 -> 검증된 AnalysisDoc. rejected는 고정 사유 코드만
    (모델 원문 값은 절대 담지 않는다 -- ground.py와 동일 원칙, D4). """
    rejected: list[str] = []

    overview = raw.get("overview")
    if not isinstance(overview, str):
        overview = ""
        rejected.append("MISSING_OVERVIEW")

    structure: list[StructureArea] = []
    for item in raw.get("structure") or []:
        if not isinstance(item, Mapping):
            rejected.append("INVALID_STRUCTURE_ITEM")
            continue
        area, role, files = item.get("area"), item.get("role"), item.get("files")
        if not isinstance(area, str) or not isinstance(role, str) or not isinstance(files, list):
            rejected.append("INVALID_STRUCTURE_ITEM")
            continue
        valid_files = tuple(f for f in files if isinstance(f, str) and f in allowed_paths)
        structure.append(StructureArea(area=area, files=valid_files, role=role))

    decision_points: list[DecisionPoint] = []
    seen: set[tuple[str, str]] = set()
    for item in raw.get("decision_points") or []:
        if not isinstance(item, Mapping):
            rejected.append("INVALID_ITEM_SHAPE")
            continue

        title, file, symbol, why = item.get("title"), item.get("file"), item.get("symbol"), item.get("why_it_matters")
        related_teach = item.get("related_teach")

        if not isinstance(file, str) or file not in allowed_paths:
            rejected.append("UNKNOWN_FILE")
            continue
        if not isinstance(symbol, str) or not symbol.strip():
            rejected.append("MISSING_SYMBOL")
            continue
        if not isinstance(title, str) or not title.strip():
            rejected.append("MISSING_TITLE")
            continue
        if not isinstance(why, str) or not why.strip():
            rejected.append("MISSING_WHY")
            continue

        if related_teach is not None and related_teach not in allowed_teach_ids:
            rejected.append("UNKNOWN_TEACH_ID_NULLED")  # 항목 자체는 버리지 않고 이 필드만 null로
            related_teach = None

        key = (file, symbol)
        if key in seen:
            rejected.append("DUPLICATE_DECISION_POINT")
            continue
        seen.add(key)

        decision_points.append(DecisionPoint(title=title, file=file, symbol=symbol, why_it_matters=why, related_teach=related_teach))

    risks = tuple(r for r in (raw.get("risks") or []) if isinstance(r, str) and r.strip())

    doc = AnalysisDoc(overview=overview, structure=tuple(structure), decision_points=tuple(decision_points), risks=risks)
    return doc, tuple(rejected)


def render_markdown(doc: AnalysisDoc) -> str:
    """ 검증된 AnalysisDoc -> 사람이 읽는 markdown 문자열. LLM 호출 없이 결정론적으로
    조립한다(D2) -- analysis_document_markdown 필드가 이 함수의 유일한 소비자다. """
    lines = ["# 코드 분석 문서", "", "## 개요", doc.overview or "(개요 없음)", ""]

    if doc.structure:
        lines.append("## 구조")
        for s in doc.structure:
            files_str = ", ".join(s.files) if s.files else "(파일 없음)"
            lines.append(f"- **{s.area}** ({files_str}): {s.role}")
        lines.append("")

    if doc.decision_points:
        lines.append("## 판단이 개입된 지점")
        for i, dp in enumerate(doc.decision_points, start=1):
            lines.append(f"{i}. **{dp.title}** -- `{dp.file}`")
            lines.append(f"   {dp.why_it_matters}")
        lines.append("")

    if doc.risks:
        lines.append("## 위험 요소")
        for r in doc.risks:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _placeholder_stages() -> list[dict[str, Any]]:
    return [
        {
            "axis_code": axis,
            "question_text": _QUESTION_GEN_PLACEHOLDER,
            "flagged": True,
            "hints": [
                {"hint_level": 1, "hint_text": "질문 생성 스테이지 미탑재 -- 플레이스홀더"},
                {"hint_level": 2, "hint_text": "질문 생성 스테이지 미탑재 -- 플레이스홀더"},
            ],
        }
        for axis in _AXIS_CODES
    ]


def build_problems(
    decision_points: Sequence[DecisionPoint],
    files_by_path: Mapping[str, str],
    *,
    extractor_version: str,
    question_budget: int,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """ 검증된 decision_points -> Problem dict 목록 (D2: 구조 그대로, 변환 없음).

    symbol을 실제 줄로 못 찾으면(D5) 그 decision_point는 버린다 -- 근거 없이
    문제를 낼 수 없다. ungrounded에 어떤 파일/스테이지에서 실패했는지 남긴다
    (모델의 자유 서술이 아니라 우리가 이미 검증한 file 값 + symbol 앞부분뿐이라
    이 목록은 디버깅용 진단이지 ground.py의 "절대 노출 금지" 대상이 아니다).
    """
    candidates = decision_points[:question_budget]
    problems: list[dict[str, Any]] = []
    ungrounded: list[str] = []
    total = len(candidates)

    for i, dp in enumerate(candidates):
        text = files_by_path.get(dp.file, "")
        location = locate_symbol(text, dp.symbol) if text else None
        if location is None:
            ungrounded.append(f"{dp.file}:{dp.symbol[:40]!r}")
            continue

        line_start, line_end = location
        snippet = slice_snippet(text, line_start, line_end)
        priority = round((total - i) / total, 4) if total else 1.0

        problems.append({
            "problem_id": str(uuid.uuid4()),
            "problem_no": len(problems) + 1,
            "status": "READY",
            "problem_type": "DESIGN_CHOICE",  # decision_points는 정의상 판단 개입 지점
            "priority": priority,
            "question_focus_item_id": None,
            "source_path": dp.file,
            "line_start": line_start,
            "line_end": line_end,
            "code_snippet": snippet,
            "evidence_hash": evidence_hash(snippet),
            "extractor_version": extractor_version,
            "references": [],
            "stages": _placeholder_stages(),
        })

    return problems, tuple(ungrounded)


def _build_code_block(files_by_path: Mapping[str, str], selected_paths: Sequence[str]) -> str:
    parts = [f"### {path}\n```\n{files_by_path.get(path, '')}\n```" for path in selected_paths]
    return "\n\n".join(parts)


def _build_teaches_block(teaches: Sequence[Mapping[str, Any]]) -> str:
    if not teaches:
        return "(교안 후보 없음)"
    lines = []
    for t in teaches:
        tid = t.get("id", "")
        label = t.get("label", "")
        unit = t.get("unitId", "")
        pages = t.get("sourcePages", [])
        lines.append(f"- id={tid} unit={unit} pages={pages}: {label}")
    return "\n".join(lines)


def run_analysis_doc(
    *,
    files_by_path: Mapping[str, str],
    selected_paths: Sequence[str],
    teaches: Sequence[Mapping[str, Any]],
    model_code: str,
    budget: CallBudget,
    job_id: str,
    chat_fn: Callable[..., ChatResult] = chat,
) -> tuple[AnalysisDoc, tuple[str, ...], list[AiUsage]]:
    """ 반환: (검증된 AnalysisDoc, ground 거부 사유들, AiUsage 목록)

    실패 시(호출 실패/예산 소진/JSON 파싱 실패) EMPTY_DOC + FAILED 기록 0~1건을
    반환한다 -- 나머지 파이프라인(problems=[], analysis_document_markdown="(개요 없음)")은
    계속 진행된다(D6의 crew.py와 같은 강등 철학).
    """
    ai_usage: list[AiUsage] = []
    if not selected_paths or budget.max_llm_calls < 1:
        return EMPTY_DOC, (), ai_usage

    allowed_paths = frozenset(selected_paths)
    allowed_teach_ids = frozenset(t.get("id") for t in teaches if t.get("id"))

    stage = load_stage("p05", "p05-3")
    code_block = _build_code_block(files_by_path, selected_paths)
    code_limit = stage.truncation.get("code_block", len(code_block))
    teaches_limit = stage.truncation.get("teaches_block", 2000)

    values = {
        "code_block": code_block[:code_limit],
        "teaches_block": _build_teaches_block(teaches)[:teaches_limit],
    }
    messages = render(stage, values)
    max_tokens = param_default(stage, "max_tokens") or 2400
    temperature = param_default(stage, "temperature") or 0.0

    timer = LlmCallTimer(
        budget.feature_code, model_code, source_type=budget.source_type, source_id=job_id, attempt_no=1,
    )
    try:
        with timer:
            result = chat_fn(
                model_code=model_code, messages=messages, max_tokens=max_tokens, temperature=temperature,
                max_attempts=budget.max_attempts_per_call, timeout_s=budget.timeout_s,
            )
        parsed = extract_json_object(result.content)
    except Exception as exc:  # noqa: BLE001 -- D6: 실패는 빈 문서로 강등, job을 안 죽인다
        ai_usage.append(timer.build(status="FAILED", failure_code=classify_failure_code(exc)))
        return EMPTY_DOC, (), ai_usage

    ai_usage.append(timer.build(
        input_token_count=result.input_tokens,
        output_token_count=result.output_tokens,
        cached_token_count=result.cached_tokens,
        status="SUCCEEDED",
    ))

    doc, rejected = parse_analysis_doc_response(parsed, allowed_paths, allowed_teach_ids)
    return doc, rejected, ai_usage
