""" D11: codemap 브랜치는 attribution 브랜치를 하드 import하지 않는다

feature/own-commit-attribution이 아직 머지되지 않은 상태에서도 codemap 패키지가
그대로 동작해야 한다는 것을, "import가 없다"는 사실 자체를 AST로 검사해서 증명한다
(코드 리뷰로 사람이 매번 확인하는 대신).
"""
import ast
from pathlib import Path


def test_codemap_package_does_not_import_attribution():
    codemap_dir = Path(__file__).resolve().parents[1] / "app" / "engines" / "codemap"
    for path in codemap_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.engines.attribution"), (
                        f"{path} imports app.engines.attribution -- D11 위반"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.engines.attribution"), (
                    f"{path} imports app.engines.attribution -- D11 위반"
                )


def test_rank_works_with_no_attribution_module_installed():
    """ attribution 패키지가 sys.modules에 전혀 없어도(즉 설치/머지 전이어도) codemap이 동작한다 """
    import sys

    assert "app.engines.attribution" not in sys.modules

    from app.engines.codemap.graph import build_import_graph
    from app.engines.codemap.models import RepoFile, Weights
    from app.engines.codemap.rank import rank_files

    files = [RepoFile(path="a.py", ext=".py", size_bytes=1, line_count=1, text="x=1")]
    graph = build_import_graph(files)
    w = Weights(fan_in=1, entry_point=1, path_depth=1, size=1, own_commit=1)
    ranked = rank_files(files, graph, weights=w)  # attribution 인자 생략 -- 기본값 None
    assert ranked[0].path == "a.py"
