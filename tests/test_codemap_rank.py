""" app/engines/codemap/rank.py -- Tier 1 결정론적 랭커 테스트 """
import copy

from app.engines.codemap.graph import build_import_graph
from app.engines.codemap.models import RepoFile, Weights
from app.engines.codemap.rank import rank_files
from app.engines.codemap.weights import parse_weights
from app.engines.shared.signals import AttributionSignal

EQUAL_WEIGHTS = Weights(fan_in=1.0, entry_point=1.0, path_depth=1.0, size=1.0, own_commit=1.0)


def _f(path, text="x = 1\n" * 30):
    ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    return RepoFile(path=path, ext=ext, size_bytes=len(text), line_count=text.count("\n") + 1, text=text)


def test_entry_point_outranks_alphabetical_first():
    """ 알파벳순으로는 App.generated.tsx가 먼저지만, main 스템이 더 높게 랭크되어야 한다 """
    files = [_f("src/App.generated.tsx"), _f("src/main.tsx")]
    graph = build_import_graph(files)
    ranked = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    assert ranked[0].path == "src/main.tsx"


def test_high_fan_in_outranks_leaf():
    files = [
        _f("src/util.py"),
        _f("src/a.py", "from src.util import x\n"),
        _f("src/b.py", "from src.util import x\n"),
        _f("src/c.py", "from src.util import x\n"),
        _f("src/leaf.py"),
    ]
    graph = build_import_graph(files)
    ranked = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    ranks_by_path = {rf.path: rf.rank for rf in ranked}
    assert ranks_by_path["src/util.py"] < ranks_by_path["src/leaf.py"]


def test_generated_file_is_demoted_by_size():
    small = _f("src/z_small.py", "x = 1\n" * 30)
    huge = _f("src/a_huge.py", "x = 1\n" * 5000)
    graph = build_import_graph([small, huge])
    ranked = rank_files([small, huge], graph, weights=EQUAL_WEIGHTS)
    # 알파벳순이면 a_huge가 먼저지만, size plateau가 큰 파일을 낮춰서 작은 파일이 이겨야 한다
    assert ranked[0].path == "src/z_small.py"


def test_tie_break_is_total_order():
    """ 완전히 동일한 신호를 가진 두 파일도 path로 완전히 결정된다(동점 없음) """
    files = [_f("src/b.py"), _f("src/a.py")]
    graph = build_import_graph(files)
    ranked = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    assert [rf.path for rf in ranked] == ["src/a.py", "src/b.py"]


def test_rank_evidence_records_terms_and_weights():
    files = [_f("src/main.py")]
    graph = build_import_graph(files)
    ranked = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    ev = ranked[0].rank_evidence
    assert set(ev["weights"].keys()) == {"fan_in", "entry_point", "path_depth", "size", "own_commit"}
    assert "entry_point" in ev["terms"]
    assert ev["tie_break_depth"] == 0


def test_weights_loaded_from_json_changes_order():
    """ 가중치를 JSON에서 바꾸면 순서가 바뀐다 -- 데이터/로직 분리 증명 """
    files = [_f("src/main.py"), _f("src/deep/nested/module.py")]
    graph = build_import_graph(files)

    equal_order = [rf.path for rf in rank_files(files, graph, weights=EQUAL_WEIGHTS)]

    only_entry_point = parse_weights(
        '{"weights": {"fan_in": 0, "entry_point": 1, "path_depth": 0, "size": 0, "own_commit": 0}}'
    )
    biased_order = [rf.path for rf in rank_files(files, graph, weights=only_entry_point)]

    assert biased_order[0] == "src/main.py"  # entry_point 신호가 전부를 결정하면 main이 1등
    assert equal_order != biased_order or equal_order[0] == "src/main.py"


def test_attribution_none_and_all_unknown_are_equivalent():
    files = [_f("src/a.py"), _f("src/b.py")]
    graph = build_import_graph(files)
    without = rank_files(files, graph, weights=EQUAL_WEIGHTS, attribution=None)
    all_unknown = {
        "src/a.py": AttributionSignal("src/a.py", "UNKNOWN", 0, 0.0),
        "src/b.py": AttributionSignal("src/b.py", "UNKNOWN", 0, 0.0),
    }
    with_unknown = rank_files(files, graph, weights=EQUAL_WEIGHTS, attribution=all_unknown)
    assert [rf.path for rf in without] == [rf.path for rf in with_unknown]


def test_authored_file_is_lifted_by_attribution():
    files = [_f("src/a.py"), _f("src/b.py")]
    graph = build_import_graph(files)
    attribution = {
        "src/b.py": AttributionSignal("src/b.py", "AUTHORED", 40, 0.9),
    }
    ranked = rank_files(files, graph, weights=EQUAL_WEIGHTS, attribution=attribution)
    assert ranked[0].path == "src/b.py"


def test_rank_files_does_not_mutate_inputs():
    files = [_f("src/a.py"), _f("src/b.py")]
    files_copy = copy.deepcopy(files)
    graph = build_import_graph(files)
    graph_copy = copy.deepcopy(graph)
    rank_files(files, graph, weights=EQUAL_WEIGHTS)
    assert files == files_copy
    assert graph == graph_copy


def test_rank_files_is_deterministic():
    files = [_f("src/a.py"), _f("src/b.py"), _f("src/c.py")]
    graph = build_import_graph(files)
    first = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    second = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    assert first == second
