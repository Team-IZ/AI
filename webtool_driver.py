# ⚠️ 삭제 금지 (Phase 2까지) — 이 파일의 로직은 app/core/pipeline_runner.py로 이관됐지만,
# 파일 자체는 아직 지울 수 없다. standalone 목업 프론트가 런타임에 이 파일을 fetch해
# Pyodide 가상 파일시스템에 써넣기 때문이다: shared/p02-engine.js:302
#   const driverRes = await fetch("../webtool_driver.py");
# (다만 FastAPI 정적 서빙 하에서는 그 fetch가 이미 404라 목업 제출 흐름은 현재 비동작이다.)
# Phase 2에서 submission.html을 FastAPI 분석 API 호출로 교체하고 Pyodide 경로를
# 제거할 때, 이 파일과 shared/p02-engine.js의 fetch를 함께 없앤다.
# 서버 코드에서는 이 파일을 import하지 말 것 — 정본은 app/core/pipeline_runner.py다.
#
# D-E (PLAN.md): the only new Python in this tool. Everything else it calls
# (two_tier_scan.py, score_findings.py, and their judgment/*.py dependencies) is the
# real, unmodified pipeline source, fetched at runtime from raw.githubusercontent.com
# and written into Pyodide's virtual filesystem at the exact paths those files expect
# (they all resolve sibling files via os.path.dirname(__file__), so path fidelity matters).
# This driver's only job is: apply UI parameter overrides as module attribute writes
# (never touching the files themselves), then call the real scan()/score() functions.
import json
import sys


def apply_overrides(overrides_json):
    """overrides_json: {"two_tier_scan": {"SRC_EXTS": [...], ...}, "score_findings": {...}}"""
    import two_tier_scan
    import score_findings
    import importance_rank  # D194: RANK_WEIGHT_QV/RANK_WEIGHT_RISK/RANK_WEIGHT_DI overrides

    modules = {"two_tier_scan": two_tier_scan, "score_findings": score_findings, "importance_rank": importance_rank}
    overrides = json.loads(overrides_json) if overrides_json else {}
    applied = []
    for module_name, params in overrides.items():
        module = modules.get(module_name)
        if module is None:
            continue
        for key, value in (params or {}).items():
            if hasattr(module, key):
                # string_list params arrive as JS arrays -> Python lists; the real code
                # expects tuples for SRC_EXTS/LOCAL_IMPORT_PREFIXES (used with .startswith()/
                # str.endswith(), both of which accept a tuple OR would accept a list too --
                # kept as tuple for exact type parity with the original module defaults).
                if isinstance(getattr(module, key), tuple) and isinstance(value, list):
                    value = tuple(value)
                if isinstance(getattr(module, key), set) and isinstance(value, list):
                    value = set(value)
                setattr(module, key, value)
                applied.append(f"{module_name}.{key}")
    return applied


def run_scan(repo_root, overrides_json="{}"):
    import two_tier_scan
    import score_findings

    applied = apply_overrides(overrides_json)
    scan_result = two_tier_scan.scan(repo_root)
    judgment_result = score_findings.score(scan_result, repo_root)
    return json.dumps(
        {"scan": scan_result, "judgment": judgment_result, "overrides_applied": applied},
        ensure_ascii=False,
    )
