"""내재화된 분석 파이프라인(AI/pipeline/) 호출 래퍼.

목업의 webtool_driver.py(Pyodide용 드라이버)의 apply_overrides/run_scan 로직을
서버용으로 이관한 것이다. 파이프라인 소스 자체는 절대 수정하지 않는다 (PLAN §4).

import 방식: Pyodide 로더·webtool_driver와 동일하게 pipeline/ 하위의
cognition/, judgment/, feedback/, pipeline/ 디렉터리와 pipeline 루트를
sys.path에 추가하는 flat 방식을 쓴다 — 모듈들이 flat import
(`import two_tier_scan` 등)와 `os.path.dirname(__file__)` 기준 형제 파일
탐색을 전제로 하기 때문이다 (pipeline/VENDORED.md 참고).
"""
import json
import sys
import threading
from pathlib import Path
from typing import Any

# AI/ 레포 루트 = 이 파일 기준 두 단계 위
_AI_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_ROOT = _AI_ROOT / "pipeline"

# flat import 전제 디렉터리 (VENDORED.md의 검증 방식과 동일)
_PIPELINE_SUBDIRS = ("cognition", "judgment", "feedback", "pipeline")

_setup_lock = threading.Lock()
_setup_done = False


def setup_pipeline_paths() -> None:
    """pipeline/ 하위 디렉터리들을 sys.path에 추가한다 (멱등)."""
    global _setup_done
    with _setup_lock:
        if _setup_done:
            return
        paths = [PIPELINE_ROOT] + [PIPELINE_ROOT / d for d in _PIPELINE_SUBDIRS]
        for p in paths:
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)
        _setup_done = True


def is_pipeline_loaded() -> bool:
    """스캔·판단 모듈이 import 가능하고 scan/score가 callable인지 확인."""
    try:
        setup_pipeline_paths()
        import score_findings
        import two_tier_scan

        return callable(getattr(two_tier_scan, "scan", None)) and callable(
            getattr(score_findings, "score", None)
        )
    except Exception:
        return False


def apply_overrides(overrides: dict[str, dict[str, Any]] | str | None) -> list[str]:
    """모듈 어트리뷰트 오버라이드 적용 (webtool_driver.apply_overrides 이관).

    overrides: {"two_tier_scan": {"SRC_EXTS": [...], ...}, "score_findings": {...},
                "importance_rank": {...}}
    파일은 건드리지 않고 이미 import된 모듈의 속성만 덮어쓴다.
    """
    setup_pipeline_paths()
    import importance_rank  # D194: RANK_WEIGHT_* overrides
    import score_findings
    import two_tier_scan

    modules = {
        "two_tier_scan": two_tier_scan,
        "score_findings": score_findings,
        "importance_rank": importance_rank,
    }
    if isinstance(overrides, str):
        overrides = json.loads(overrides) if overrides else {}
    overrides = overrides or {}

    applied: list[str] = []
    for module_name, params in overrides.items():
        module = modules.get(module_name)
        if module is None:
            continue
        for key, value in (params or {}).items():
            if hasattr(module, key):
                # 원본 모듈 기본값이 tuple/set인 파라미터는 타입을 맞춰준다
                # (webtool_driver와 동일한 처리 — SRC_EXTS 등은 tuple 전제).
                if isinstance(getattr(module, key), tuple) and isinstance(value, list):
                    value = tuple(value)
                if isinstance(getattr(module, key), set) and isinstance(value, list):
                    value = set(value)
                setattr(module, key, value)
                applied.append(f"{module_name}.{key}")
    return applied


def run_scan(
    repo_root: str, overrides: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """실제 파이프라인 scan() → score() 실행 (webtool_driver.run_scan 이관).

    webtool_driver는 Pyodide 경계 때문에 JSON 문자열을 반환했지만,
    서버에서는 dict를 그대로 반환한다.
    """
    setup_pipeline_paths()
    import score_findings
    import two_tier_scan

    applied = apply_overrides(overrides)
    scan_result = two_tier_scan.scan(repo_root)
    judgment_result = score_findings.score(scan_result, repo_root)
    return {
        "scan": scan_result,
        "judgment": judgment_result,
        "overrides_applied": applied,
    }
