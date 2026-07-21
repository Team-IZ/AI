"""분석 job 실행·상태 추적 (명세 §3.1/§3.2).

명세 §1 "FastAPI의 상태": 진행 중인 작업만 휘발성으로 보유한다 — 따라서 job 저장소는
인메모리 dict이며 프로세스 재시작 시 유실된다(Spring이 재요청하면 된다).

작업공간 수명 (§3.3 / B8):
- 코드 원문은 `settings.workspace_dir/<job_id>/` 안에만 둔다.
- **분석 완료 시점에 지우지 않는다.** §3.3은 원문을 "분석~세션 진행 동안 유지"라고
  규정하고, Phase 3의 세션이 질문 생성 근거로 같은 원문을 읽어야 하기 때문이다.
- 대신 job 생성 때마다 `workspace_ttl_sec`(기본 24h, B8 미확정 잠정값)이 지난
  디렉터리를 청소한다. 실패한 job의 작업공간은 즉시 지운다(세션이 열릴 일이 없다).
"""
from __future__ import annotations

import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core import attribution as attribution_mod
from app.core import collect, findings as findings_mod
from app.core import pipeline_runner

logger = logging.getLogger(__name__)


@dataclass
class AnalysisJob:
    job_id: str
    attempt_id: str | None = None
    submission_id: str | None = None
    # B3: 필드만 수용·보관한다. 실제 콜백 전송은 **미구현**(Phase 2 범위 밖).
    callback_url: str | None = None
    status: str = "QUEUED"  # QUEUED | ANALYZING | READY | PARTIAL | FAILED
    failure_reason: str | None = None
    error: dict[str, Any] | None = None  # §2 공통 에러 형식
    result: dict[str, Any] | None = None
    # P02는 LLM을 호출하지 않으므로 항상 빈 배열이다
    # (p02-engine.js 헤더: "P02 has zero LLM calls").
    ai_usage: list[dict[str, Any]] = field(default_factory=list)
    workspace: Path | None = None
    created_at: float = field(default_factory=time.time)

    def to_response(self) -> dict[str, Any]:
        """§3.2 응답 본문."""
        body: dict[str, Any] = {
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "submission_id": self.submission_id,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "result": self.result,
            "ai_usage": self.ai_usage,
        }
        if self.error:
            body["error"] = self.error
        return body


class JobStore:
    """인메모리 job 저장소 (§1: FastAPI 상태는 휘발성)."""

    def __init__(self) -> None:
        self._jobs: dict[str, AnalysisJob] = {}

    def create(self, **kwargs: Any) -> AnalysisJob:
        job_id = str(uuid.uuid4())
        job = AnalysisJob(job_id=job_id, **kwargs)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> AnalysisJob | None:
        return self._jobs.get(job_id)


def sweep_workspaces(root: Path, ttl_sec: int) -> None:
    """TTL이 지난 작업공간 디렉터리를 삭제한다 (§3.3 / B8)."""
    if not root.is_dir():
        return
    cutoff = time.time() - ttl_sec
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:  # 다른 프로세스가 이미 지웠거나 권한 문제 — 무시
            logger.debug("workspace sweep skipped: %s", child, exc_info=True)


def run_analysis(
    job: AnalysisJob,
    *,
    method: str,
    repo_url: str | None,
    branch: str | None,
    zip_bytes: bytes | None,
    extraction_scope: str,
    commit_email: str | None,
    question_budget: int,
) -> None:
    """백그라운드에서 실행되는 분석 본체. 결과·실패를 job에 기록한다.

    입력: `job`(진행 상태를 in-place로 갱신할 `AnalysisJob`), `method`
    ("GITHUB_URL"|"ZIP_WITH_GITLOG"), `repo_url`/`branch`(GITHUB_URL용),
    `zip_bytes`(ZIP_WITH_GITLOG용), `extraction_scope`("TOTAL"|"OWN_COMMIT"),
    `commit_email`(OWN_COMMIT용), `question_budget`(int).
    출력 없음(반환값 대신 `job.status`/`job.result`/`job.error`를 직접 갱신) —
    호출자(`create_analysis`)가 `BackgroundTasks`로 fire-and-forget 실행하므로
    예외를 밖으로 던지면 아무도 못 잡는다. 그래서 실패는 전부 `_fail()`로
    job 상태에 기록하고 함수 자체는 항상 정상 반환한다.
    """
    job.status = "ANALYZING"
    workspace = job.workspace
    assert workspace is not None

    try:
        # 1) 소스 수집
        if method == "GITHUB_URL":
            source = collect.collect_from_github(repo_url or "", branch, workspace)
        else:
            source = collect.collect_from_zip(zip_bytes or b"", workspace)

        if not source.files:
            # A-? / 명세 §3.2 failure_reason 예시의 NO_SOURCE
            raise collect.CollectError(
                "NO_SOURCE",
                "스캔 대상 소스 파일을 찾지 못했습니다 "
                f"(지원 확장자: {', '.join(collect.SRC_EXTS)}, .ipynb는 코드 셀만 추출)",
            )

        # 2) 커밋 귀속 (OWN_COMMIT일 때만)
        applied_scope = extraction_scope
        scope_fallback = False
        fallback_reason = None
        attribution: attribution_mod.Attribution | None = None
        files = source.files

        if extraction_scope == "OWN_COMMIT":
            attribution = _attribute(source, commit_email or "")
            if attribution is None:
                # MEAS-02A A-2: 커밋 로그 자체가 없다 → TOTAL 폴백
                applied_scope = "TOTAL"
                scope_fallback = True
                fallback_reason = "NO_COMMIT_LOG"
            elif attribution.commit_count == 0:
                # MEAS-02A A-1: 로그는 있는데 본인 커밋이 0건 → 폴백이 아니라 실패
                raise collect.CollectError(
                    "ATTRIBUTION_REQUIRED",
                    f"'{commit_email}' 명의의 커밋을 찾지 못했습니다",
                )
            else:
                scoped = _scope_files(files, attribution.attributed_files)
                if not scoped:
                    # 커밋은 있으나 변경 파일이 전부 비소스(문서·설정 등)였다
                    applied_scope = "TOTAL"
                    scope_fallback = True
                    fallback_reason = "NO_ATTRIBUTED_SOURCE_FILES"
                else:
                    files = scoped

        # 3) 파이프라인 실행 (pipeline/ 무수정 — PLAN §4)
        target_root = workspace / "target"
        collect.materialize(files, target_root)
        raw = pipeline_runner.run_scan(str(target_root))

        # 4) 명세 §3.2 형태로 변환
        api_findings = findings_mod.to_api_findings(
            raw["judgment"].get("findings", []), files
        )
        job.result = {
            "applied_scope": applied_scope,
            "scope_fallback": scope_fallback,
            "fallback_reason": fallback_reason,
            "attribution": _attribution_payload(attribution, applied_scope),
            "commit_sha": source.commit_sha,
            "findings": api_findings,
            # MEAS-02B A-5: 유효 DP가 N보다 적으면 축소된 값
            "question_count_planned": min(question_budget, len(api_findings)),
        }
        # PARTIAL은 "결과는 쓸 수 있으나 요청대로는 아님"으로 해석했다 —
        # 현재 유일한 사례가 OWN_COMMIT→TOTAL 폴백이다(명세에 PARTIAL 조건이
        # 열거돼 있지 않아 이 해석은 백엔드 확인 대상).
        job.status = "PARTIAL" if scope_fallback else "READY"

    except collect.CollectError as exc:
        _fail(job, exc.code, exc.message, exc.retryable)
    except Exception as exc:  # 예기치 못한 내부 오류
        logger.exception("analysis job %s failed", job.job_id)
        _fail(job, "ANALYSIS_FAILED", str(exc), retryable=True)


def _fail(job: AnalysisJob, code: str, message: str, retryable: bool) -> None:
    job.status = "FAILED"
    job.failure_reason = code
    job.error = {"code": code, "message": message, "retryable": retryable}
    # 실패한 job에는 세션이 붙지 않으므로 원문을 즉시 지운다 (§3.3).
    if job.workspace:
        shutil.rmtree(job.workspace, ignore_errors=True)


def _attribute(
    source: collect.CollectedSource, commit_email: str
) -> attribution_mod.Attribution | None:
    """B5의 귀속 수단을 순서대로 시도한다: `.git` → 동봉 export → 없음(None)."""
    root = source.source_root
    if root is None:
        return None
    from_git = attribution_mod.from_git_repo(root, commit_email)
    if from_git is not None:
        return from_git
    return attribution_mod.from_export_files(root, commit_email)


def _scope_files(files: dict[str, str], attributed: list[str]) -> dict[str, str]:
    """귀속된 파일만 남긴다.

    귀속 경로는 레포 루트 기준이고 수집 키도 동일 기준이지만, ZIP이 한 겹
    감싸는 경우가 흔해 basename 폴백도 함께 본다.
    """
    attributed_set = {p.replace("\\", "/") for p in attributed}
    attributed_names = {p.split("/")[-1] for p in attributed_set}
    scoped = {}
    for rel_path, content in files.items():
        # .ipynb는 가상 ".py"가 덧붙은 키이므로 원 경로로 되돌려 비교한다.
        probe = rel_path[:-3] if rel_path.lower().endswith(".ipynb.py") else rel_path
        if probe in attributed_set or probe.split("/")[-1] in attributed_names:
            scoped[rel_path] = content
    return scoped


def _attribution_payload(
    attribution: attribution_mod.Attribution | None, applied_scope: str
) -> dict[str, Any] | None:
    """§3.2: `attribution`은 OWN_COMMIT일 때만 채운다."""
    if attribution is None or applied_scope != "OWN_COMMIT":
        return None
    return {
        "attributed_files": attribution.attributed_files,
        "commit_count": attribution.commit_count,
        "verification_status": attribution.verification_status,
    }
