""" AnalysisEngine 프로토콜 구현체 -- Tier 1(+옵션 Tier 2) 코드 중요도 선별 엔진

app/engines/base.py::AnalysisEngine은 구조적 타이핑(Protocol)이라 상속이 필요
없다 -- analyze(request, zip_bytes) -> dict 시그니처만 맞으면 된다.

이 엔진이 아직 하지 않는 일: 실제 질문/힌트 생성(question-generation, 별도
feature_code), 요구사항 P/F 실제 판정. AnalysisResult 계약은 4개의 Problem
stage와 요청 requirements 개수만큼의 RequirementResult를 요구하므로, 그 자리를
"아직 판정/생성되지 않았음"을 명시하는 placeholder로 채운다(flagged=True,
verdict="F"+note) -- 조용히 통과로 기록하는 대신 검수 필요 상태로 남긴다.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Mapping

from app.config import get_settings
from app.engines.codemap import build_code_map_from_repo
from app.engines.codemap.materialize import Materializer, default_materialize_repo
from app.engines.codemap.models import CodeMapConfig
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
    ) -> None:
        self._tier2_enabled = tier2_enabled
        self._materializer = materializer
        self._attribution = attribution
        self._weights_path = weights_path

    def analyze(self, request: dict[str, Any], zip_bytes: bytes | None = None) -> dict[str, Any]:
        """ 동기 def(코루틴 아님) -- BackgroundTasks가 스레드풀에서 돌리므로 이벤트
        루프를 막지 않는다(README §4 함정 표, jobs.py의 실행 방식과 대응). """
        settings = get_settings()

        with self._materializer(request, zip_bytes, settings.workspace_dir or None) as repo_dir:
            code_map = build_code_map_from_repo(
                repo_dir,
                config=CodeMapConfig(tier2_enabled=self._tier2_enabled, model_code=request.get("model_code")),
                attribution=self._attribution,
                weights_path=self._weights_path,
                job_id=request.get("submission_id") or request.get("attempt_id") or "unknown",
            )

        applied_scope, scope_fallback, fallback_reason = self._resolve_scope(request)
        problems = self._build_problems(code_map, request)
        requirement_results = self._build_requirement_results(request)

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
            "analysis_document_markdown": self._build_analysis_document(code_map),
            "requirement_results": requirement_results,
            "problems": problems,
            "question_count_planned": len(problems),
            "ai_usage": [u.model_dump(by_alias=False) for u in code_map["ai_usage"]],
        }

    def _resolve_scope(self, request: dict[str, Any]) -> tuple[str, bool, str | None]:
        requested = request.get("extraction_scope", "TOTAL")
        if requested == "OWN_COMMIT" and self._attribution is None:
            return (
                "TOTAL", True,
                "attribution 모듈 미탑재(feature/own-commit-attribution 미병합) -- TOTAL로 대체",
            )
        return requested, False, None

    def _build_analysis_document(self, code_map: dict[str, Any]) -> str:
        lines = ["# 코드 중요도 선별 결과", ""]
        lines.append(
            "Tier 2(크루 재랭킹) 적용: " + ("예" if code_map["tier2_applied"] else "아니오 (Tier 1 결정론적 순위만 사용)")
        )
        if code_map["tier2_rejected"]:
            lines.append(f"Tier 2 응답 중 거부된 항목: {len(code_map['tier2_rejected'])}건")
        lines.append("")
        lines.append("| 순위 | 경로 | Tier1 순위 | 역할 |")
        lines.append("|---|---|---|---|")
        for e in code_map["entries"]:
            lines.append(f"| {e.rank} | {e.path} | {e.tier1_rank} | {e.role or '-'} |")
        return "\n".join(lines)

    def _build_problems(self, code_map: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
        question_budget = request.get("question_budget", 4)
        files_by_path = code_map["files_by_path"]

        problems = []
        for i, entry in enumerate(code_map["entries"][:question_budget], start=1):
            text = files_by_path.get(entry.path, "")
            problems.append(self._build_problem(entry, text, problem_no=i))
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
