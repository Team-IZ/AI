""" app/engines/codemap/ground.py -- closed-vocabulary 검증 테스트

핵심 주장: 크루가 후보 목록 밖의 경로나 고정 열거값 밖의 role/reason_code를
주장해도, 그 자유 텍스트가 CodeMap까지 도달하는 경로가 전혀 없다.
"""
from app.engines.codemap.ground import merge_rerank, parse_rerank
from app.engines.codemap.models import RankedFile

ALLOWED_PATHS = frozenset({"src/main.py", "src/util.py", "src/leaf.py"})


def _rf(path, rank, score=1.0):
    return RankedFile(path=path, rank=rank, rank_score=score, signals={}, rank_evidence={})


def test_rejects_unknown_path():
    raw = {"changes": [{"path": "src/nonexistent.py", "role": "UTIL", "delta_rank": 1, "reason_code": "IMPORT_HUB"}]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    assert any("UNKNOWN_PATH" in r for r in rejected)


def test_rejects_path_outside_shortlist_even_if_plausible_looking():
    """ 진짜 있을 법한 이름이라도 '보여준 후보 목록'에 없으면 거부한다 """
    raw = {"changes": [{"path": "src/routes.tsx", "role": "ROUTING", "delta_rank": 2, "reason_code": "IMPORT_HUB"}]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    assert any("UNKNOWN_PATH" in r for r in rejected)


def test_rejects_unknown_role():
    raw = {"changes": [{"path": "src/main.py", "role": "SUPER_IMPORTANT", "delta_rank": 1, "reason_code": "IMPORT_HUB"}]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    assert any("UNKNOWN_ROLE" in r for r in rejected)


def test_rejects_unknown_reason_code():
    raw = {"changes": [{"path": "src/main.py", "role": "UTIL", "delta_rank": 1, "reason_code": "BECAUSE_I_SAID_SO"}]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    assert any("UNKNOWN_REASON_CODE" in r for r in rejected)


def test_rejects_non_integer_delta_rank():
    raw = {"changes": [{"path": "src/main.py", "role": "UTIL", "delta_rank": "a lot", "reason_code": "IMPORT_HUB"}]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    assert any("INVALID_DELTA_RANK" in r for r in rejected)


def test_rejects_duplicate_path_claims():
    raw = {"changes": [
        {"path": "src/main.py", "role": "UTIL", "delta_rank": 1, "reason_code": "IMPORT_HUB"},
        {"path": "src/main.py", "role": "CONFIG", "delta_rank": 2, "reason_code": "IMPORT_HUB"},
    ]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert len(claims) == 1
    assert claims[0].role == "UTIL"  # 첫 번째만 채택
    assert any("DUPLICATE_PATH" in r for r in rejected)


def test_changes_not_a_list_is_rejected_wholesale():
    claims, rejected = parse_rerank({"changes": "not a list"}, ALLOWED_PATHS)
    assert claims == ()
    assert rejected


def test_valid_claim_is_accepted():
    raw = {"changes": [{"path": "src/main.py", "role": "ENTRY_POINT", "delta_rank": 3, "reason_code": "ENTRY_POINT_CONFIRMED"}]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert len(claims) == 1
    assert claims[0].path == "src/main.py"
    assert rejected == ()


def test_free_prose_never_reaches_output():
    """ role/reason_code에 자유 서술을 넣으면 그 텍스트 자체가 결과 어디에도 안 남는다 """
    raw = {"changes": [{
        "path": "src/main.py",
        "role": "this file is definitely the most important one in the whole repo",
        "delta_rank": 5,
        "reason_code": "trust me, I read the whole codebase",
    }]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    joined = " ".join(rejected)
    assert "trust me" not in joined
    assert "definitely the most important" not in joined


def test_accepts_curriculum_reason_codes():
    """ D13: 교안/요구사항 때문에 올린다는 주장을 표현할 자리가 열거값에 있다 """
    for code in ("MATCHES_TEACH", "MATCHES_REQUIREMENT"):
        raw = {"changes": [{"path": "src/main.py", "role": "DOMAIN_LOGIC", "delta_rank": 2, "reason_code": code}]}
        claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
        assert len(claims) == 1, code
        assert claims[0].reason_code == code
        assert rejected == ()


def test_curriculum_reason_code_does_not_relax_whole_claim_discard():
    """ D13이 열거값을 늘렸다고 '한 필드만 틀리면 나머지는 살린다'로 바뀌지 않는다 --
    reason_code가 맞아도 role이 틀리면 항목 전체가 버려진다(하우스 룰 유지) """
    raw = {"changes": [{
        "path": "src/main.py", "role": "MOST_IMPORTANT_FILE",
        "delta_rank": 3, "reason_code": "MATCHES_TEACH",
    }]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    assert any("UNKNOWN_ROLE" in r for r in rejected)


def test_teach_id_shaped_reason_code_is_still_rejected():
    """ 모델이 열거값 대신 교안 id/문구를 reason_code에 넣으면 그대로 버려진다 --
    새 어휘를 추가한 것이지 자유 텍스트를 허용한 게 아니다 """
    raw = {"changes": [{
        "path": "src/main.py", "role": "DOMAIN_LOGIC", "delta_rank": 1,
        "reason_code": "MATCHES_TEACH:t-42 (예외 처리 교안과 직접 대응)",
    }]}
    claims, rejected = parse_rerank(raw, ALLOWED_PATHS)
    assert claims == ()
    assert any("UNKNOWN_REASON_CODE" in r for r in rejected)
    assert "예외 처리" not in " ".join(rejected)  # 원문은 어디에도 안 남는다


def test_clamps_delta_rank_to_max_shift():
    from app.engines.codemap.models import CrewClaim

    tier1 = (_rf("src/main.py", 1), _rf("src/util.py", 2), _rf("src/leaf.py", 3))
    claims = (CrewClaim(path="src/leaf.py", role="ENTRY_POINT", delta_rank=100, reason_code="ENTRY_POINT_CONFIRMED"),)
    entries = merge_rerank(tier1, claims, max_rank_shift=1)
    leaf_entry = next(e for e in entries if e.path == "src/leaf.py")
    # delta_rank=100은 max_rank_shift=1로 클램프 -> rank 3에서 최대 1칸(2위)까지만 올라간다
    assert leaf_entry.rank == 2


def test_empty_crew_output_yields_tier1_order():
    tier1 = (_rf("src/main.py", 1), _rf("src/util.py", 2), _rf("src/leaf.py", 3))
    entries = merge_rerank(tier1, (), max_rank_shift=3)
    assert [e.path for e in entries] == ["src/main.py", "src/util.py", "src/leaf.py"]
    assert all(e.role is None and e.reason_code is None for e in entries)
    assert all(e.rank == e.tier1_rank for e in entries)


def test_merge_is_deterministic():
    from app.engines.codemap.models import CrewClaim

    tier1 = (_rf("src/main.py", 1), _rf("src/util.py", 2), _rf("src/leaf.py", 3))
    claims = (CrewClaim(path="src/leaf.py", role="UTIL", delta_rank=2, reason_code="IMPORT_HUB"),)
    first = merge_rerank(tier1, claims, max_rank_shift=3)
    second = merge_rerank(tier1, claims, max_rank_shift=3)
    assert first == second


def test_merge_does_not_mutate_inputs():
    import copy

    tier1 = (_rf("src/main.py", 1), _rf("src/util.py", 2))
    tier1_copy = copy.deepcopy(tier1)
    merge_rerank(tier1, (), max_rank_shift=1)
    assert tier1 == tier1_copy
