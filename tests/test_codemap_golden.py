""" 실제 조립부(build_code_map_from_repo)를 통한 골든 테스트

D1의 실측 버그(78개 중 1개만 생존, 원인: 알파벳순 그리디 채우기)를 재현하는
축소판 시나리오가 핵심(test_react_style_repo_survives_the_alphabetical_bug).
나머지는 각 언어/구조에서 이 조립 경로 전체(collect -> graph -> rank -> shortlist)가
깨지지 않는지 확인한다.
"""
from app.engines.codemap import build_code_map_from_repo
from app.engines.codemap.models import CodeMapConfig


def _write(root, relpath, content):
    full = root / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def test_react_style_repo_survives_the_alphabetical_bug(tmp_path):
    """ D1 회귀 테스트: 알파벳상 가장 먼저 오는 큰 파일이 예산을 다 먹어
    실제 라우팅 파일(routes.tsx)이 탈락하던 원본 버그의 축소 재현 """
    # 알파벳순으로 가장 먼저 오지만 아무도 참조하지 않는 거대한 생성 파일
    _write(tmp_path, "src/App.generated.tsx", "export const BLOB = `" + ("x" * 15_000) + "`;\n")
    # 실제 라우팅 진입점 -- 여러 파일이 참조하는 실질적으로 중요한 파일
    _write(tmp_path, "src/routes.tsx", "export function Routes() { return null; }\n")
    _write(tmp_path, "src/pages/Home.tsx", "import { Routes } from '../routes';\nexport const Home = () => Routes;\n")
    _write(tmp_path, "src/pages/About.tsx", "import { Routes } from '../routes';\nexport const About = () => Routes;\n")
    # 커밋된 빌드 산출물 (실제로는 SKIP_DIRS에 걸려 수집 자체가 안 됨)
    _write(tmp_path, "dist/bundle.js", "/* built output, should never be scanned */\n" * 500)

    result = build_code_map_from_repo(
        str(tmp_path), config=CodeMapConfig(max_shortlist_files=40, max_shortlist_chars=12_000)
    )

    assert "src/routes.tsx" in result["shortlist"], "routes.tsx는 반드시 숏리스트에 살아남아야 한다"
    assert not any(p.startswith("dist/") for p in result["shortlist"] + result["truncated"]), (
        "dist/ 산출물은 애초에 수집조차 되면 안 된다"
    )
    # 생성 파일이 예산을 독점해 다른 모든 파일을 밀어내지 않는다는 것도 함께 확인
    assert len(result["shortlist"]) >= 2


def test_java_default_package_assignment(tmp_path):
    _write(tmp_path, "Main.java", "public class Main {\n    Student s = new Student();\n}\n")
    _write(tmp_path, "Student.java", "public class Student {\n    private int score;\n}\n")
    _write(tmp_path, "GradeCalculator.java", "public class GradeCalculator {\n    int compute() { return 1; }\n}\n")

    result = build_code_map_from_repo(str(tmp_path))
    assert result["file_count"] == 3
    assert result["ranked"][0]["path"] in {"Main.java", "Student.java", "GradeCalculator.java"}


def test_python_fastapi_entry_point_ranks_high(tmp_path):
    _write(tmp_path, "main.py", "from app.routers import users\napp = object()\n")
    _write(tmp_path, "app/routers/users.py", "def list_users():\n    return []\n")
    _write(tmp_path, "app/models.py", "class User:\n    pass\n")

    result = build_code_map_from_repo(str(tmp_path))
    assert result["ranked"][0]["path"] == "main.py"


def test_committed_build_output_outside_dist_is_excluded(tmp_path):
    _write(tmp_path, "public/main.a1b2c3d4e5.js", "/* content-hashed bundle */\n")
    _write(tmp_path, "src/real.js", "export const x = 1;\n")

    result = build_code_map_from_repo(str(tmp_path))
    all_paths = {r["path"] for r in result["ranked"]}
    assert "public/main.a1b2c3d4e5.js" not in all_paths
    assert "src/real.js" in all_paths


def test_single_commit_repo_ranking_unaffected_by_all_unknown_attribution(tmp_path):
    from app.engines.shared.signals import AttributionSignal

    _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "b.py", "y = 1\n")

    without = build_code_map_from_repo(str(tmp_path))
    all_unknown = {
        "a.py": AttributionSignal("a.py", "UNKNOWN", 0, 0.0),
        "b.py": AttributionSignal("b.py", "UNKNOWN", 0, 0.0),
    }
    with_unknown = build_code_map_from_repo(str(tmp_path), attribution=all_unknown)

    assert [r["path"] for r in without["ranked"]] == [r["path"] for r in with_unknown["ranked"]]


# --- D13: 교안/요구사항이 랭킹 경로에 실제로 도달하는지 (조립부 전체 통과) ------

def test_omitting_curriculum_is_identical_to_pre_d13_result(tmp_path):
    """ 교안/요구사항을 안 주면 D13 이전과 결과가 완전히 같다.

    조립부까지 포함해서 확인하는 이유: rank.py 단위 테스트는 curriculum=None을
    직접 넘기지만, 실제 운영 요청은 teaches=[]를 넘긴다 -- 그 빈 리스트가
    match_curriculum()에서 None으로 바뀌어 같은 기권 경로를 타는지가 진짜 계약이다.
    """
    _write(tmp_path, "main.py", "from app.util import helper\nhelper()\n")
    _write(tmp_path, "app/util.py", "def helper():\n    return 1\n")

    without = build_code_map_from_repo(str(tmp_path))
    with_empty = build_code_map_from_repo(str(tmp_path), teaches=[], requirements=[])

    assert without["ranked"] == with_empty["ranked"]  # rank_score까지 동일
    assert without["curriculum"] is None
    assert with_empty["curriculum"] is None


def test_curriculum_diagnostics_are_recorded_even_though_weight_is_zero(tmp_path):
    """ 운영 가중치가 0.0이라 순위는 안 바뀌지만, 무엇에 걸렸는지는 기록된다 --
    PR-3 가중치 재보정이 쓸 원자료(rank.py D13의 "관측은 하되 판단엔 안 쓴다") """
    _write(tmp_path, "main.py", "from app.util import helper\nhelper()\n")
    _write(tmp_path, "app/util.py", "def helper():\n    return 1\n")
    _write(tmp_path, "app/settlement.py", "def settlement_ledger():\n    return 1\n")

    baseline = build_code_map_from_repo(str(tmp_path))
    result = build_code_map_from_repo(
        str(tmp_path),
        teaches=[{"id": "t1", "label": "settlement ledger 정산"}],
        requirements=[{"requirementId": "r1", "text": "정산 원장을 구현한다"}],
    )

    # 커밋된 codemap_weights.json의 curriculum=0.0 -> 순위는 그대로여야 한다
    assert [r["path"] for r in baseline["ranked"]] == [r["path"] for r in result["ranked"]]

    # 그러나 신호 자체는 계산돼 결과에 남는다
    assert result["curriculum"] is not None
    assert result["curriculum"]["matches"].get("app/settlement.py")
    assert "settlement" in result["curriculum"]["matched_terms"]

    settlement = next(r for r in result["ranked"] if r["path"] == "app/settlement.py")
    assert settlement["signals"]["curriculum_raw"] >= 1.0
    assert settlement["rank_evidence"]["weights"]["curriculum"] == 0.0


def test_result_dict_is_json_serializable_with_curriculum(tmp_path):
    """ __main__.py --json이 그대로 직렬화한다 -- 새 키가 그 경로를 깨지 않는지 """
    import json

    _write(tmp_path, "main.py", "def settlement_ledger():\n    return 1\n")
    result = build_code_map_from_repo(
        str(tmp_path), teaches=[{"id": "t1", "label": "settlement ledger 정산"}]
    )
    payload = {k: v for k, v in result.items() if k not in {"entries", "ai_usage"}}
    assert json.loads(json.dumps(payload, ensure_ascii=False))["curriculum"]["item_count"] == 1
