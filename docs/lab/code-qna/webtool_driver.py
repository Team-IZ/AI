# D-E (PLAN.md): the only new Python in this tool. Everything else it calls
# (two_tier_scan.py, score_findings.py, and their judgment/*.py dependencies) is the
# real, unmodified pipeline source, fetched at runtime from raw.githubusercontent.com
# and written into Pyodide's virtual filesystem at the exact paths those files expect
# (they all resolve sibling files via os.path.dirname(__file__), so path fidelity matters).
# This driver's only job is: apply UI parameter overrides as module attribute writes
# (never touching the files themselves), then call the real scan()/score() functions.
import json
import sys


SHARED_SCAN_CONSTANTS = ("SKIP_DIRS", "SKIP_DIR_PREFIXES", "SKIP_DIR_SUFFIXES")


def apply_overrides(overrides_json):
    """overrides_json: {"two_tier_scan": {"SRC_EXTS": [...], ...}, "score_findings": {...}}"""
    import two_tier_scan
    import score_findings
    import idiom_filter  # D195-fix: was missing here, so its own SKIP_DIRS copy could
    # never be overridden -- p02-1's panel edits reached two_tier_scan only, leaving
    # score_findings and idiom_filter on defaults (an internally-inconsistent scan state
    # no real deployment can produce). Present now so the SHARED_SCAN_CONSTANTS broadcast
    # below can reach it too.
    import importance_rank  # D194: RANK_WEIGHT_QV/RANK_WEIGHT_RISK/RANK_WEIGHT_DI overrides

    modules = {
        "two_tier_scan": two_tier_scan,
        "score_findings": score_findings,
        "idiom_filter": idiom_filter,
        "importance_rank": importance_rank,
    }
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

                # D195-fix: SKIP_DIRS/SKIP_DIR_PREFIXES/SKIP_DIR_SUFFIXES are intentionally
                # duplicated across all three scan-tree-walking modules (idiom_filter.py
                # can't import them from score_findings.py -- circular import). The UI only
                # ever sends these under the "two_tier_scan" bucket (p02-1's stage mapping),
                # so without this broadcast an edit here silently never reached the other two
                # copies. Broadcast keeps all three modules in the one state a trainee sees.
                if key in SHARED_SCAN_CONSTANTS:
                    for other_name, other_module in modules.items():
                        if other_name == module_name or not hasattr(other_module, key):
                            continue
                        other_value = value
                        if isinstance(getattr(other_module, key), tuple) and isinstance(other_value, list):
                            other_value = tuple(other_value)
                        if isinstance(getattr(other_module, key), set) and isinstance(other_value, list):
                            other_value = set(other_value)
                        setattr(other_module, key, other_value)
                        applied.append(f"{other_name}.{key} (mirrored from {module_name})")
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
