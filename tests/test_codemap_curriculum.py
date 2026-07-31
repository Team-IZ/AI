""" app/engines/codemap/curriculum.py -- 교안/요구사항 토큰 겹침 (D13)

핵심 주장은 "잘 맞춘다"가 아니라 **"확신 없으면 기권한다"**이다. 이 신호가
poc_full의 Tier B(고정 키워드 위험 트리거)처럼 폐기되지 않으려면, 어휘가 고정돼
있지 않다는 것과 변별력 없는 단어에서 발화하지 않는다는 것 두 가지가 테스트로
고정돼 있어야 한다.
"""
import copy

from app.engines.codemap.curriculum import match_curriculum
from app.engines.codemap.models import RepoFile


def _f(path, text="x = 1\n" * 30):
    ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    return RepoFile(path=path, ext=ext, size_bytes=len(text), line_count=text.count("\n") + 1, text=text)


# --- 기권(abstention) 경로 -------------------------------------------------

def test_no_files_abstains():
    assert match_curriculum([], [{"id": "t1", "label": "settlement ledger"}], []) is None


def test_no_curriculum_abstains():
    assert match_curriculum([_f("src/a.py")], [], []) is None
    assert match_curriculum([_f("src/a.py")], None, None) is None


def test_unrelated_curriculum_abstains():
    """ 대조군: 코드와 아무 상관 없는 교안은 발화하지 않는다(0건 매칭 -> None).

    이게 "무관한 입력에 조용히 그럴듯한 점수를 붙이지 않는다"는 주장이다 --
    실측(2026-07-31, 이 저장소 app/ 49파일)에서도 무관 교안은 기권했다.
    """
    files = [_f("src/order.py", "def create_order():\n    return 1\n")]
    teaches = [
        {"id": "t1", "label": "sourdough fermentation schedule"},
        {"id": "t2", "label": "espresso tamping pressure"},
    ]
    assert match_curriculum(files, teaches, []) is None


def test_short_tokens_are_dropped_and_reported():
    """ 짧은 토큰('id','api','for')은 우연 일치율이 높아 매칭에서 뺀다 """
    files = [_f("src/a.py", "api = 1\nid = 2\n")]
    match = match_curriculum(files, [{"id": "t1", "label": "api id for db"}], [])
    assert match is None  # 유효 토큰이 하나도 안 남아 기권


def test_generic_token_is_dropped_by_corpus_document_frequency():
    """ 변별력 게이트: 거의 모든 파일에 있는 단어는 그 제출물에서 버려진다.

    이 게이트가 "고정 stopword 목록"이 아니라 **제출물마다 실측되는 값**이라는 게
    Tier B와의 결정적 차이다 -- 어떤 단어가 흔한지를 우리가 미리 정해두지 않는다.
    """
    # 'repository'가 10개 파일 전부에 들어 있다 -> df=100% -> 변별력 없음
    files = [_f(f"src/mod{i}.py", "class repository:\n    pass\n") for i in range(10)]
    match = match_curriculum(files, [{"id": "t1", "label": "repository pattern"}], [])
    assert match is None


def test_tiny_repo_does_not_collapse_the_discriminative_gate():
    """ 회귀 테스트: 비율 게이트만 쓰면 소규모 제출물에서 의미가 뒤집힌다.

    파일이 2개면 '한 파일에만 있는 토큰'도 df=50% > 30%라 '너무 흔함'으로 버려져
    신호가 언제나 기권했다. _MIN_GENERIC_DF_FILES 하한이 그걸 막는다 -- 학생
    제출물은 파일이 몇 개뿐인 경우가 흔하므로 이건 예외가 아니라 주 경로다.
    """
    files = [_f("src/a.py", "x = 1\n"), _f("src/ledger.py", "def settlement():\n    pass\n")]
    match = match_curriculum(files, [{"id": "t1", "label": "settlement reconciliation"}], [])
    assert match is not None
    assert set(match.matches) == {"src/ledger.py"}


def test_same_token_survives_when_it_is_actually_discriminative():
    """ 위 테스트와 같은 단어라도 소수 파일에만 있으면 살아남는다 -- 게이트가
    단어 자체가 아니라 그 저장소에서의 분포를 본다는 증거(같은 어휘, 다른 결과) """
    files = [_f(f"src/mod{i}.py", "x = 1\n") for i in range(9)]
    files.append(_f("src/store.py", "class repository:\n    pass\n"))
    match = match_curriculum(files, [{"id": "t1", "label": "repository pattern"}], [])
    assert match is not None
    assert set(match.matches) == {"src/store.py"}


# --- 매칭 동작 -------------------------------------------------------------

def test_matches_teach_label_against_file_content():
    files = [_f("src/a.py", "x = 1\n"), _f("src/ledger.py", "def settlement():\n    pass\n")]
    match = match_curriculum(files, [{"id": "t1", "label": "settlement reconciliation"}], [])
    assert match is not None
    assert match.matches == {"src/ledger.py": 1}
    assert match.item_count == 1


def test_matches_against_path_not_only_content():
    """ 'src/auth/JwtFilter.java'처럼 경로에만 개념이 드러나는 경우도 잡는다 """
    files = [_f("src/plain.py", "x = 1\n"), _f("src/authentication/handler.py", "x = 2\n")]
    match = match_curriculum(files, [{"id": "t1", "label": "authentication flow"}], [])
    assert match is not None
    assert set(match.matches) == {"src/authentication/handler.py"}


def test_camel_case_identifier_is_split_for_matching():
    """ verifyJwtToken 같은 식별자가 'token' 교안에 걸린다(부분 문자열이 아니라 부분 토큰) """
    files = [_f("src/a.py", "x = 1\n"), _f("src/auth.py", "def verifyAccessToken():\n    pass\n")]
    match = match_curriculum(files, [{"id": "t1", "label": "access token 검증"}], [])
    assert match is not None
    assert set(match.matches) == {"src/auth.py"}


def test_snake_case_identifier_is_split_for_matching():
    files = [_f("src/a.py", "x = 1\n"), _f("src/repo.py", "def user_repository():\n    pass\n")]
    match = match_curriculum(files, [{"id": "t1", "label": "repository layer"}], [])
    assert match is not None
    assert set(match.matches) == {"src/repo.py"}


def test_substring_alone_does_not_match():
    """ 'list'가 'listener'에 부분 문자열로 들어 있다고 매칭되면 안 된다 --
    토큰 경계 기준이라는 것을 고정한다(Tier B식 substring 스캔과의 차이) """
    files = [_f("src/a.py", "x = 1\n"), _f("src/events.py", "def addListener():\n    pass\n")]
    assert match_curriculum(files, [{"id": "t1", "label": "linked list 구현"}], []) is None


def test_requirement_text_is_matched_like_a_teach():
    files = [_f("src/a.py", "x = 1\n"), _f("src/refund.py", "def refund():\n    pass\n")]
    match = match_curriculum(files, [], [{"requirementId": "r1", "text": "refund 처리를 구현한다"}])
    assert match is not None
    assert set(match.matches) == {"src/refund.py"}


def test_teach_and_requirement_hits_are_counted_separately():
    """ 같은 파일이 teach 1건 + requirement 1건에 걸리면 hit=2 (건수 기반) """
    files = [
        _f("src/a.py", "x = 1\n"),
        _f("src/refund.py", "def refund_settlement():\n    pass\n"),
    ]
    match = match_curriculum(
        files,
        [{"id": "t1", "label": "settlement 개념"}],
        [{"requirementId": "r1", "text": "refund 기능"}],
    )
    assert match is not None
    assert match.matches["src/refund.py"] == 2
    assert match.item_count == 2


def test_canonical_name_is_accepted_as_teach_label_alias():
    """ 같은 openapi.json 안에서 Teach 스키마는 canonicalName을 쓴다 -- 조용히
    0건이 되느니 둘 다 읽는다(curriculum.py::_collect_items의 주석) """
    files = [_f("src/a.py", "x = 1\n"), _f("src/ledger.py", "def settlement():\n    pass\n")]
    match = match_curriculum(files, [{"id": "t1", "canonicalName": "settlement 개념"}], [])
    assert match is not None
    assert set(match.matches) == {"src/ledger.py"}


def test_malformed_curriculum_items_are_skipped_not_crashed():
    """ Spring이 보낸 dict가 아닌 값이 섞여도 죽지 않는다(외부 입력 방어) """
    files = [_f("src/ledger.py", "def settlement():\n    pass\n"), _f("src/a.py", "x = 1\n")]
    match = match_curriculum(
        files,
        ["문자열", None, 42, {"id": "t1", "label": "settlement 개념"}],
        [None, {"requirementId": "r1"}],  # text 없는 requirement
    )
    assert match is not None
    assert set(match.matches) == {"src/ledger.py"}


# --- 순수성/결정론 ---------------------------------------------------------

def test_is_deterministic_and_does_not_mutate_inputs():
    files = [_f("src/a.py", "x = 1\n"), _f("src/ledger.py", "def settlement():\n    pass\n")]
    teaches = [{"id": "t1", "label": "settlement reconciliation"}]
    files_copy, teaches_copy = copy.deepcopy(files), copy.deepcopy(teaches)

    first = match_curriculum(files, teaches, [])
    second = match_curriculum(files, teaches, [])

    assert first == second
    assert files == files_copy
    assert teaches == teaches_copy


def test_matches_mapping_is_sorted_for_stable_serialization():
    """ dict 순서가 삽입 순서에 좌우되면 같은 입력의 JSON 직렬화가 달라진다
    (D2: 프로세스 경계를 넘어도 같은 답) """
    files = [
        _f("src/zeta.py", "def settlement():\n    pass\n"),
        _f("src/alpha.py", "def settlement():\n    pass\n"),
        _f("src/other.py", "x = 1\n"),
    ]
    match = match_curriculum(files, [{"id": "t1", "label": "settlement reconciliation"}], [])
    assert match is not None
    assert list(match.matches) == sorted(match.matches)


def test_dropped_terms_are_reported_for_calibration():
    """ D14: 왜 어떤 단어가 무시됐는지가 진단값으로 남는다 -- PR-3 가중치 재보정의 원자료 """
    files = [_f(f"src/mod{i}.py", "class repository:\n    pass\n") for i in range(9)]
    files.append(_f("src/store.py", "def settlement():\n    pass\n"))
    match = match_curriculum(
        files, [{"id": "t1", "label": "repository pattern for settlement of id"}], []
    )
    assert match is not None
    assert "repository" in match.dropped_generic  # 10개 중 9개 파일 -> 변별력 없음
    assert "id" in match.dropped_short  # 길이 게이트
    assert "settlement" in match.matched_terms
