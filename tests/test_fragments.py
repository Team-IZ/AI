""" symbol → 줄 번호 산정(T7b). LLM 주장과 산정된 사실을 가르는 지점이다. """
from app.engines.analysis import fragments

FILES = {
    "app/api/analyses.py": (
        "from fastapi import APIRouter\n"          # 1
        "\n"                                        # 2
        "async def create_analysis(\n"              # 3
        "    request: Request,\n"                   # 4
        "    engine: AnalysisEngine = Depends(get_analysis_engine),\n"   # 5
        ") -> AnalysisAccepted:\n"                  # 6
        "    return AnalysisAccepted()\n"           # 7
        "\n"                                        # 8
        "def other():\n"                            # 9
        "    pass\n"                                # 10
    ),
}


def test_single_line_symbol_is_located():
    """기본 경로 — 한 줄을 그대로 인용한 경우."""
    r = fragments.locate_symbol(FILES, "app/api/analyses.py", "def other():")

    assert r["valid"] is True
    assert r["line_start"] == 9


def test_multiline_symbol_falls_back_to_first_line():
    """실측 재현(p04-1, 2026-07-31) — 모델이 시그니처 전체를 여러 줄로 붙여 왔다.

    줄 단위 매칭만 하면 통째로 버려진다. 첫 줄로 살려야 한다.
    """
    symbol = ("async def create_analysis(\n request: Request,\n"
              " engine: AnalysisEngine = Depends(get_analysis_engine),\n) -> AnalysisAccepted:")

    r = fragments.locate_symbol(FILES, "app/api/analyses.py", symbol)

    assert r["valid"] is True
    assert r["line_start"] == 3
    assert r["line_end"] == 7   # 들여쓰기가 얕아지는 7행 전까지


def test_normalized_match_survives_whitespace_drift():
    """모델이 들여쓰기를 뭉개 인용해도 찾아야 한다."""
    r = fragments.locate_symbol(FILES, "app/api/analyses.py", "request:   Request,")

    assert r["valid"] is True
    assert r["line_start"] == 4


def test_basename_fallback():
    """LLM이 경로를 줄여 인용하는 일이 잦다."""
    r = fragments.locate_symbol(FILES, "analyses.py", "def other():")

    assert r["valid"] is True
    assert r["file"] == "app/api/analyses.py"


def test_invented_symbol_is_rejected():
    """없는 코드를 지목하면 버린다 — 이게 환각 방지의 전부다."""
    r = fragments.locate_symbol(FILES, "app/api/analyses.py", "def vanished():")

    assert r["valid"] is False
    assert "찾을 수 없음" in r["reason"]


def test_fragment_carries_snippet_and_context():
    """codeSnippet은 문맥 없는 원문이다 — evidenceHash가 이 값 기준이다."""
    f = fragments.extract_fragment(FILES, "app/api/analyses.py", "def other():")

    assert f["snippet"].startswith("def other():")
    assert "from fastapi" not in f["snippet"]      # 문맥은 snippet에 안 섞인다
    assert f["context_start"] < f["line_start"]     # 문맥은 따로 있다

def test_symbol_with_a_broken_tail_still_locates():
    """🔴 LLM은 코드를 끝까지 정확히 옮겨 적지 못한다. 실호출에서 나온 3건 그대로다.

    앞부분이 맞으면 시작 줄은 확정된다 — 오타 한 글자에 개념 하나를 통째로
    "코드에 없음"으로 박으면 오퍼레이터가 고른 개념이 조용히 빠진다.
    """
    files = {"pipeline/graph.py": "\n".join([
        "def route(state):",
        '    worker = state.get("next_worker", "FINISH")',
        "    return worker",
    ])}

    for quoted in (r'worker = state.get("next_worker", "FINISH\))',
                   'worker = state.get("next_worker", "FINISH"}'):
        located = fragments.locate_symbol(files, "pipeline/graph.py", quoted)
        assert located["valid"], quoted
        assert located["line_start"] == 2


def test_short_prefix_does_not_match_anything():
    """`worker` 같은 짧은 조각은 엉뚱한 줄에 먼저 걸린다 — 하한이 그걸 막는다."""
    files = {"a.py": "worker = 1\nother = 2\n"}

    assert fragments.locate_symbol(files, "a.py", "worker(((")["valid"] is False


def test_broken_backtick_quote_is_repaired():
    """🔴 학생이 보는 텍스트다. 닫는 백틱이 한 글자 일찍 찍혀 나온다."""
    code = '    worker = state.get("next_worker", "FINISH")\n    return worker\n'
    broken = '이 코드 `worker = state.get("next_worker", "FINISH"`)가 실행될 때'

    fixed = fragments.repair_code_quotes(broken, code)

    assert fixed == '이 코드 `worker = state.get("next_worker", "FINISH")`가 실행될 때'


def test_repair_leaves_text_alone_when_unsure():
    """LLM 문장을 우리가 다시 쓰지 않는다 — 애매하면 그대로 둔다."""
    code = "a = compute(1)\nb = compute(2)\n"

    # 짧은 인용은 어느 줄에나 걸린다.
    assert fragments.repair_code_quotes("`a =` 를 보세요", code) == "`a =` 를 보세요"
    # 이미 온전한 인용은 안 건드린다.
    assert fragments.repair_code_quotes("`a = compute(1)` 를", code) == "`a = compute(1)` 를"
    # 접두사로 두 줄이 걸리면 어느 쪽인지 모른다.
    assert fragments.repair_code_quotes("`compute(` 확인", code) == "`compute(` 확인"


def test_repair_does_not_expand_a_deliberate_partial_quote():
    """긴 줄에서 일부만 인용한 정상 문장은 그대로 둔다. 손상 서명은 '꼬리가 밖에 있음'이다."""
    code = 'worker = state.get("next_worker", "FINISH")\n'
    text = '`state.get("next_worker"` 부분을 보세요'   # 꼬리가 이어지지 않는다

    assert fragments.repair_code_quotes(text, code) == text
