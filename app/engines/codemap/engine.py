""" AnalysisEngine 프로토콜 구현체 -- Tier 1(+옵션 Tier 2) 코드 중요도 선별 + 코드 분석 문서 생성

app/engines/base.py::AnalysisEngine은 구조적 타이핑(Protocol)이라 상속이 필요
없다 -- analyze(request, zip_bytes) -> dict 시그니처만 맞으면 된다.

이 엔진이 아직 하지 않는 일: 실제 질문/힌트 생성(question-generation, 별도
feature_code), 요구사항 P/F 실제 판정. AnalysisResult 계약은 4개의 Problem
stage와 요청 requirements 개수만큼의 RequirementResult를 요구하므로, 그 자리를
"아직 판정/생성되지 않았음"을 명시하는 placeholder로 채운다(flagged=True,
verdict="F"+note) -- 조용히 통과로 기록하는 대신 검수 필요 상태로 남긴다.

D7 (2026-07-31): problems는 두 원천을 합친다 -- 1순위는 analysis_doc.run_analysis_doc()이
만든 실제 decision_points(코드 분석 문서 생성 스테이지, p05-3), 부족하면(호출 실패,
또는 decision_points가 question_budget보다 적게 grounding됨) codemap의 Tier1/2
랭킹만으로 만든 placeholder problem으로 채운다 -- 이 강등도 D6(크루 실패시 Tier1
순위로 강등)과 같은 철학: 코드 분석 문서 생성이 실패해도 job 전체가 FAILED로
떨어지지 않고 최소한 "어떤 파일이 중요한지"는 항상 응답에 남는다.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Mapping

from app.config import get_settings
from app.engines.codemap import build_code_map_from_repo
from app.engines.codemap.analysis_doc import build_problems as build_problems_from_decision_points
from app.engines.codemap.analysis_doc import render_markdown, run_analysis_doc
from app.engines.codemap.diagram import run_diagram_stage
from app.engines.codemap.materialize import Materializer, default_materialize_repo
from app.engines.codemap.models import CodeMapConfig
from app.engines.shared.budget import load_budget
from app.engines.shared.signals import AttributionSignal

EXTRACTOR_VERSION = "codemap-0.1.0"

_AXIS_CODES = ("L1", "L2", "L3", "L4")

_PROBLEM_TYPE_BY_ROLE = {
    "ENTRY_POINT": "DESIGN_CHOICE",
    "ROUTING": "DESIGN_CHOICE",
    "DOMAIN_LOGIC": "COMPLEXITY_HOTSPOT",
    "DATA_ACCESS": "RISK_POINT",
    "INTEGRATION": "EXTERNAL_INTEGRATION",
    "CONFIG": "DESIGN_CHOICE",
    "UI": "COMPLEXITY_HOTSPOT",
    "TEST": "REQUIREMENT_IMPL",
    "UTIL": "COMPLEXITY_HOTSPOT",
}
_DEFAULT_PROBLEM_TYPE = "COMPLEXITY_HOTSPOT"

_SNIPPET_MAX_LINES = 40  # app/engines/shared/evidence.py::BLOCK_MAX_LINES와 동일 상한


class CodeMapAnalysisEngine:
    """ AnalysisEngine 구조적 타이핑 구현체. 생성자 인자는 전부 테스트를 위한
    주입 지점이다 -- get_analysis_engine() 팩토리는 전부 기본값으로 만든다. """

    def __init__(
        self,
        *,
        tier2_enabled: bool = False,
        materializer: Materializer = default_materialize_repo,
        attribution: Mapping[str, AttributionSignal] | None = None,
        weights_path=None,
        analysis_doc_chat_fn=None,
        diagram_chat_fn=None,
    ) -> None:
        self._tier2_enabled = tier2_enabled
        self._materializer = materializer
        self._attribution = attribution
        self._weights_path = weights_path
        self._analysis_doc_chat_fn = analysis_doc_chat_fn  # 테스트 주입 지점. None이면 실제 chat() 사용
        self._diagram_chat_fn = diagram_chat_fn  # 테스트 주입 지점(p05-4). None이면 실제 chat() 사용

    def analyze(self, request: dict[str, Any], zip_bytes: bytes | None = None) -> dict[str, Any]:
        """ 동기 def(코루틴 아님) -- BackgroundTasks가 스레드풀에서 돌리므로 이벤트
        루프를 막지 않는다(README §4 함정 표, jobs.py의 실행 방식과 대응). """
        settings = get_settings()
        job_id = request.get("submission_id") or request.get("attempt_id") or "unknown"

        with self._materializer(request, zip_bytes, settings.workspace_dir or None) as repo_dir:
            code_map = build_code_map_from_repo(
                repo_dir,
                config=CodeMapConfig(tier2_enabled=self._tier2_enabled, model_code=request.get("model_code")),
                attribution=self._attribution,
                weights_path=self._weights_path,
                job_id=job_id,
            )

        applied_scope, scope_fallback, fallback_reason = self._resolve_scope(request)
        question_budget = request.get("question_budget", 4)
        model_code = request.get("model_code") or settings.default_model_code

        doc_kwargs = {"chat_fn": self._analysis_doc_chat_fn} if self._analysis_doc_chat_fn is not None else {}
        doc, _doc_rejected, doc_ai_usage = run_analysis_doc(
            files_by_path=code_map["files_by_path"],
            selected_paths=code_map["shortlist"],
            teaches=request.get("teaches", []),
            model_code=model_code,
            budget=load_budget("ANALYSIS_DOC"),
            job_id=job_id,
            **doc_kwargs,
        )
        doc_problems, _ungrounded = build_problems_from_decision_points(
            doc.decision_points, code_map["files_by_path"],
            extractor_version=EXTRACTOR_VERSION, question_budget=question_budget,
        )
        problems = self._fill_problems(doc_problems, code_map, question_budget)
        requirement_results = self._build_requirement_results(request)

        diagram_kwargs = {"chat_fn": self._diagram_chat_fn} if self._diagram_chat_fn is not None else {}
        mermaid_source, diagram_ai_usage = run_diagram_stage(
            doc=doc, model_code=model_code, budget=load_budget("DIAGRAM"), job_id=job_id, **diagram_kwargs,
        )

        all_ai_usage = list(code_map["ai_usage"]) + doc_ai_usage + diagram_ai_usage

        return {
            "snapshot_id": str(uuid.uuid4()),
            "snapshot_meta": {
                "content_hash": self._content_hash(code_map["files_by_path"]),
                "file_count": code_map["file_count"],
                "byte_count": sum(len(t.encode("utf-8")) for t in code_map["files_by_path"].values()),
            },
            "applied_scope": applied_scope,
            "scope_fallback": scope_fallback,
            "fallback_reason": fallback_reason,
            "commit_sha": None,
            "analysis_document_markdown": self._build_analysis_document(code_map, doc, mermaid_source),
            "requirement_results": requirement_results,
            "problems": problems,
            "question_count_planned": len(problems),
            "ai_usage": [u.model_dump(by_alias=False) for u in all_ai_usage],
        }

    def _resolve_scope(self, request: dict[str, Any]) -> tuple[str, bool, str | None]:
        requested = request.get("extraction_scope", "TOTAL")
        if requested == "OWN_COMMIT" and self._attribution is None:
            return (
                "TOTAL", True,
                "attribution 모듈 미탑재(feature/own-commit-attribution 미병합) -- TOTAL로 대체",
            )
        return requested, False, None

    def _build_analysis_document(self, code_map: dict[str, Any], doc, mermaid_source: str = "") -> str:
        """ 실제 코드 분석 문서(render_markdown) + 구조도(p05-4, 성공 시만) + codemap의
        선정 근거(Tier1/2 표)를 이어붙인다. mermaid_source가 빈 문자열이면(D6 강등)
        구조도 섹션 자체를 안 넣는다 -- 백엔드 스키마 변경 없이 자유 텍스트 안에
        ```mermaid 펜스 블록으로만 추가된다(D3, diagram.py 모듈 docstring). """
        parts = [render_markdown(doc)]
        if mermaid_source:
            parts += ["## 구조도", "", "```mermaid", mermaid_source, "```", ""]
        parts += ["## codemap 선정 근거 (Tier 1/2)", ""]
        parts.append(
            "Tier 2(크루 재랭킹) 적용: " + ("예" if code_map["tier2_applied"] else "아니오 (Tier 1 결정론적 순위만 사용)")
        )
        if code_map["tier2_rejected"]:
            parts.append(f"Tier 2 응답 중 거부된 항목: {len(code_map['tier2_rejected'])}건")
        parts.append("")
        parts.append("| 순위 | 경로 | Tier1 순위 | 역할 |")
        parts.append("|---|---|---|---|")
        for e in code_map["entries"]:
            parts.append(f"| {e.rank} | {e.path} | {e.tier1_rank} | {e.role or '-'} |")
        return "\n".join(parts)

    def _fill_problems(
        self, doc_problems: list[dict[str, Any]], code_map: dict[str, Any], question_budget: int
    ) -> list[dict[str, Any]]:
        """ D7: 분석 문서의 실제 decision_points에서 만든 problem을 우선 쓰고,
        모자라면(호출 실패/grounding 실패로 0~N개만 나옴) codemap 랭킹 기반
        placeholder로 나머지를 채운다 -- 절대 problems=[]로 완전히 비지 않는다. """
        problems = list(doc_problems[:question_budget])
        if len(problems) >= question_budget:
            return problems

        used_paths = {p["source_path"] for p in problems}
        files_by_path = code_map["files_by_path"]
        for entry in code_map["entries"]:
            if len(problems) >= question_budget:
                break
            if entry.path in used_paths:
                continue
            text = files_by_path.get(entry.path, "")
            problems.append(self._build_problem(entry, text, problem_no=len(problems) + 1))
            used_paths.add(entry.path)

        return problems

    def _build_problem(self, entry, text: str, *, problem_no: int) -> dict[str, Any]:
        from app.engines.shared.evidence import evidence_hash, slice_snippet

        line_count = text.count("\n") + 1
        end_line = min(line_count, _SNIPPET_MAX_LINES)
        snippet = slice_snippet(text, 1, end_line) if text else ""

        placeholder_note = (
            "코드 중요도 선별 단계 -- 이 파일이 분석 대상으로 선택된 이유는 확인됐지만, "
            "실제 질문은 아직 생성되지 않았습니다(question-generation 스테이지 대기, D8/D10 참고)."
        )
        stages = [
            {
                "axis_code": axis,
                "question_text": placeholder_note,
                "flagged": True,
                "hints": [
                    {"hint_level": 1, "hint_text": "질문 생성 스테이지 미탑재 -- 플레이스홀더"},
                    {"hint_level": 2, "hint_text": "질문 생성 스테이지 미탑재 -- 플레이스홀더"},
                ],
            }
            for axis in _AXIS_CODES
        ]

        return {
            "problem_id": str(uuid.uuid4()),
            "problem_no": problem_no,
            "status": "READY",
            "problem_type": _PROBLEM_TYPE_BY_ROLE.get(entry.role, _DEFAULT_PROBLEM_TYPE),
            "priority": round(entry.tier1_score, 6),
            "question_focus_item_id": None,
            "source_path": entry.path,
            "line_start": 1 if text else 0,
            "line_end": end_line if text else 0,
            "code_snippet": snippet,
            "evidence_hash": evidence_hash(snippet),
            "extractor_version": EXTRACTOR_VERSION,
            "references": [],
            "stages": stages,
        }

    def _build_requirement_results(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for req in request.get("requirements", []):
            req_id = req.get("requirementId") or req.get("requirement_id") or str(uuid.uuid4())
            results.append({
                "requirement_id": req_id,
                "verdict": "F",
                "evidence": None,
                "note": "not judged by code-map stage (요구사항 P/F 판정은 별도 스테이지)",
            })
        return results

    @staticmethod
    def _content_hash(files_by_path: Mapping[str, str]) -> str:
        hasher = hashlib.sha256()
        for path in sorted(files_by_path):
            hasher.update(path.encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(files_by_path[path].encode("utf-8"))
            hasher.update(b"\x00")
        return hasher.hexdigest()
