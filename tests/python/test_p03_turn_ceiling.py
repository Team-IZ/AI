"""shared/p03-engine.js maxTurns 상한 정적 회귀 테스트 (2026-08-04, redteam audit H7).

P03에는 서버측 세션/턴 카운터가 없어(C2와 근본원인 동일) 이 방어는 LabApp.setOverride
런타임 남용만 막는 best-effort 완화다 -- 파일을 직접 편집해 재배포하면 여전히 뚫린다는
점이 코드 주석에도 명시돼 있다. 그래서 여기서 확인할 수 있는 것도 "그 상한이 조용히
없어지지 않았는가"까지다 -- 진짜 강제는 서버 컴포넌트가 생겨야(C2) 가능하다.

실행: python3 -m pytest tests/python/ -q   (저장소 루트에서)
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_PATH = os.path.join(REPO_ROOT, "shared", "p03-engine.js")


def _read_engine():
    with open(ENGINE_PATH, encoding="utf-8") as f:
        return f.read()


def test_hard_turn_ceiling_constant_is_defined_and_sane():
    src = _read_engine()
    m = re.search(r"HARD_TURN_CEILING = (\d+)", src)
    assert m, "HARD_TURN_CEILING constant not found in shared/p03-engine.js"
    ceiling = int(m.group(1))
    levels_m = re.search(r'LEVELS = \[([^\]]+)\]', src)
    assert levels_m, "LEVELS array not found"
    level_count = len(levels_m.group(1).split(","))
    assert level_count <= ceiling < 1000, (
        f"HARD_TURN_CEILING={ceiling} should be a small, deliberate multiple of "
        f"LEVELS.length={level_count}, not unbounded"
    )


def test_max_turns_computation_is_clamped_by_the_ceiling():
    src = _read_engine()
    assert re.search(
        r"maxTurns = Math\.min\(LabApp\.resolveParam\([^)]*\) \|\| \d+, HARD_TURN_CEILING\)",
        src,
    ), "maxTurns no longer clamps LabApp.resolveParam()'s (client-overridable) value against HARD_TURN_CEILING"
