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