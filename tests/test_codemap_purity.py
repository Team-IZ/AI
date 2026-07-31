""" D2 순수성을 코드가 아니라 테스트로 증명한다

"스테이트풀이든 스테이트리스든 채점 로직이 안 바뀐다"는 사용자의 요구는 곧
"이 모듈들이 프로세스 경계를 넘어도 같은 답을 낸다"는 뜻이다. 아래 여섯 모듈은
graph/rank/shortlist/weights/ground/curriculum -- 파일시스템/네트워크/시계/전역상태에
의존하는 import이 하나도 없어야 한다(collect.py/crew.py/__init__.py는 의도적으로
불순한 edge라서 이 목록에 없다).

curriculum.py(D13)가 이 목록에 들어간 게 핵심이다: 교안/요구사항을 랭킹에 반영하는
결정이 D2 순수성을 깨지 않았다는 주장은 이 테스트로 고정된다 -- 나중에 누군가
"의미 매칭을 제대로 하려면 임베딩이 필요하다"며 네트워크/모델 로딩을 이 모듈에
들이면 즉시 실패한다(그 방향의 제자리는 Tier 2다, crew.py D13).
"""
import ast
from pathlib import Path

PURE_MODULES = ["graph.py", "rank.py", "shortlist.py", "weights.py", "ground.py", "curriculum.py"]
ALLOWED_TOP_LEVEL_IMPORTS = {
    "dataclasses", "typing", "math", "collections", "re", "json", "functools",
    "__future__",
    "app.engines.codemap.models", "app.engines.shared.signals",
}


def _codemap_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "app" / "engines" / "codemap"


def _imported_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_pure_modules_import_no_io():
    for filename in PURE_MODULES:
        path = _codemap_dir() / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_names(tree)
        disallowed = {n for n in imported if n not in ALLOWED_TOP_LEVEL_IMPORTS}
        assert not disallowed, f"{filename} imports disallowed modules: {disallowed}"


def test_rank_files_is_deterministic_across_repeated_calls():
    from app.engines.codemap.graph import build_import_graph
    from app.engines.codemap.models import RepoFile, Weights
    from app.engines.codemap.rank import rank_files

    files = [RepoFile(path=f"f{i}.py", ext=".py", size_bytes=10, line_count=1, text="x=1") for i in range(5)]
    graph = build_import_graph(files)
    w = Weights(fan_in=1, entry_point=1, path_depth=1, size=1, own_commit=1)

    first = rank_files(files, graph, weights=w)
    second = rank_files(files, graph, weights=w)
    assert first == second


def test_pipeline_survives_process_boundary(tmp_path):
    """ RepoFile 튜플을 JSON으로 직렬화 -> 새 호출에서 역직렬화해도 결과가 바이트 단위로 같다.

    이게 "스테이트풀 서버든 스테이트리스든 상관없다"는 성질을 실제로 표현한 테스트다:
    한 프로세스에서 만든 입력을 파일로 내보내 완전히 새 호출에 먹여도 같은 답이 나온다.
    """
    import json

    from app.engines.codemap.graph import build_import_graph
    from app.engines.codemap.models import RepoFile, Weights
    from app.engines.codemap.rank import rank_files

    files = [
        RepoFile(path="src/main.py", ext=".py", size_bytes=20, line_count=2, text="from src.util import x\n"),
        RepoFile(path="src/util.py", ext=".py", size_bytes=10, line_count=1, text="x = 1\n"),
    ]
    w = Weights(fan_in=1, entry_point=1, path_depth=1, size=1, own_commit=1)

    dump = tmp_path / "files.json"
    dump.write_text(
        json.dumps([{"path": f.path, "ext": f.ext, "size_bytes": f.size_bytes, "line_count": f.line_count, "text": f.text} for f in files]),
        encoding="utf-8",
    )

    reloaded = [RepoFile(**row) for row in json.loads(dump.read_text(encoding="utf-8"))]

    graph_a = build_import_graph(files)
    graph_b = build_import_graph(reloaded)
    ranked_a = rank_files(files, graph_a, weights=w)
    ranked_b = rank_files(reloaded, graph_b, weights=w)

    assert ranked_a == ranked_b
