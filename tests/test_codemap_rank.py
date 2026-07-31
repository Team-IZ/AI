""" app/engines/codemap/rank.py -- Tier 1 결정론적 랭커 테스트 """
import copy

from app.engines.codemap.curriculum import match_curriculum
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
    """ 교안 신호를 안 쓰는 호출의 evidence에는 curriculum이 아예 나타나지 않는다
    (D13 기권 규칙: 분모에 안 들어간 가중치는 근거에도 안 적는다) """
    files = [_f("src/main.py")]
    graph = build_import_graph(files)
    ranked = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    ev = ranked[0].rank_evidence
    assert set(ev["weights"].keys()) == {"fan_in", "entry_point", "path_depth", "size", "own_commit"}
    assert "curriculum" not in ev["terms"]
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


# --- D13: 교안/요구사항 신호 (curriculum) --------------------------------

CURRICULUM_WEIGHTS = Weights(
    fan_in=1.0, entry_point=1.0, path_depth=1.0, size=1.0, own_commit=1.0, curriculum=1.0
)


def test_absent_curriculum_is_byte_identical_to_pre_d13_behavior():
    """ D13 기권 규칙의 핵심 주장: curriculum을 안 넘기면 이 신호가 없던 시절과
    rank_score까지 완전히 같다 -- 분모(weight_sum)에서도 빠지기 때문이다.

    curriculum 가중치를 1.0으로 켠 Weights로 호출해도 결과가 같아야 한다:
    "가중치는 켜져 있지만 입력이 없다"와 "이 신호 자체가 없다"는 구분되지 않아야
    한다(그러지 않으면 교안 없는 요청의 점수가 조용히 5/6배로 바뀐다).
    """
    files = [_f("src/main.py"), _f("src/util.py"), _f("src/deep/nested/mod.py")]
    graph = build_import_graph(files)

    without_signal = rank_files(files, graph, weights=EQUAL_WEIGHTS)
    weight_on_but_no_input = rank_files(files, graph, weights=CURRICULUM_WEIGHTS, curriculum=None)

    assert without_signal == weight_on_but_no_input


def test_empty_teaches_and_requirements_yield_abstention():
    """ 빈 교안/요구사항은 match_curriculum이 None을 돌려주고, 그 None이 위 테스트가
    고정한 "완전 동일" 경로로 이어진다(조립부에서 실제로 일어나는 흐름) """
    files = [_f("src/main.py"), _f("src/util.py")]
    graph = build_import_graph(files)

    assert match_curriculum(files, [], []) is None
    assert rank_files(files, graph, weights=CURRICULUM_WEIGHTS, curriculum=match_curriculum(files, [], [])) == (
        rank_files(files, graph, weights=EQUAL_WEIGHTS)
    )


def test_curriculum_match_lifts_a_file_when_weight_is_on():
    """ 교안 개념을 담은 파일이 그렇지 않은 파일보다 위로 올라간다 """
    files = [
        _f("src/alpha.py", "def compute():\n    return 1\n" * 10),
        _f("src/beta.py", "def settlement_ledger():\n    return 1\n" * 10),
    ]
    graph = build_import_graph(files)
    teaches = [{"id": "t1", "label": "settlement ledger reconciliation"}]

    baseline = rank_files(files, graph, weights=CURRICULUM_WEIGHTS)
    assert baseline[0].path == "src/alpha.py"  # 알파벳 타이브레이크로 alpha가 먼저

    ranked = rank_files(
        files, graph, weights=CURRICULUM_WEIGHTS, curriculum=match_curriculum(files, teaches, [])
    )
    assert ranked[0].path == "src/beta.py"


def test_requirement_text_also_lifts_a_file():
    """ teaches뿐 아니라 requirements[].text도 같은 경로로 랭킹에 들어간다 """
    files = [
        _f("src/alpha.py", "def compute():\n    return 1\n" * 10),
        _f("src/beta.py", "def refund_policy():\n    return 1\n" * 10),
    ]
    graph = build_import_graph(files)
    requirements = [{"requirementId": "r1", "text": "환불 정책(refund policy)을 구현해야 한다"}]

    ranked = rank_files(
        files, graph, weights=CURRICULUM_WEIGHTS, curriculum=match_curriculum(files, [], requirements)
    )
    assert ranked[0].path == "src/beta.py"


def test_curriculum_weight_zero_does_not_reorder_but_still_records_evidence():
    """ 운영 기본값(curriculum=0.0) 검증: 순위는 안 움직이지만 근거는 기록된다.

    이게 "관측은 하되 판단에는 안 쓴다"(rank.py D13)를 코드로 고정한 테스트다 --
    PR-3 실측이 이 기록에 의존한다.
    """
    files = [
        _f("src/alpha.py", "def compute():\n    return 1\n" * 10),
        _f("src/beta.py", "def settlement_ledger():\n    return 1\n" * 10),
    ]
    graph = build_import_graph(files)
    off = Weights(fan_in=1.0, entry_point=1.0, path_depth=1.0, size=1.0, own_commit=1.0, curriculum=0.0)
    match = match_curriculum(files, [{"id": "t1", "label": "settlement ledger reconciliation"}], [])

    ranked = rank_files(files, graph, weights=off, curriculum=match)
    assert [rf.path for rf in ranked] == ["src/alpha.py", "src/beta.py"]  # 순서 그대로

    beta = next(rf for rf in ranked if rf.path == "src/beta.py")
    assert beta.signals["curriculum_raw"] == 1.0
    assert beta.rank_evidence["weights"]["curriculum"] == 0.0
    assert "curriculum" in beta.rank_evidence["terms"]
    assert "curriculum_raw" not in beta.rank_evidence["terms"]  # 원시값은 terms에 안 들어간다


def test_curriculum_signal_is_deterministic_and_does_not_mutate_inputs():
    files = [_f("src/alpha.py"), _f("src/beta.py", "def settlement_ledger(): pass\n" * 10)]
    files_copy = copy.deepcopy(files)
    graph = build_import_graph(files)
    teaches = [{"id": "t1", "label": "settlement ledger reconciliation"}]

    first = rank_files(files, graph, weights=CURRICULUM_WEIGHTS, curriculum=match_curriculum(files, teaches, []))
    second = rank_files(files, graph, weights=CURRICULUM_WEIGHTS, curriculum=match_curriculum(files, teaches, []))
    assert first == second
    assert files == files_copy
