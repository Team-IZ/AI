""" app/engines/codemap/shortlist.py -- 예산 안에서 랭크 순서로 채우는 로직 테스트

D1(원본 버그): buildCodeBlock()의 알파벳순 그리디 채우기가 첫 큰 파일에 예산을
다 뺏겨 78개 중 1개만 살아남았다. 여기서는 랭크 순서를 지키고, 예산을 넘기는
지점부터는 새치기 없이 전부 truncated로 넘긴다는 것만 검증한다(순서 자체가
옳은지는 rank.py의 책임 -- 이 모듈은 "이미 정해진 순서를 예산 안에서 어떻게
채우는가"만 담당한다).

D2(2026-08-03): 위 "새치기 없이 멈춘다" 규칙에는 예외가 하나 있다 -- 파일 하나가
혼자서 max_chars 전체보다 크면, 그 파일만 건너뛰고(멈추지 않고) 다음 랭크로 계속
진행한다. 그 파일보다 낮은 랭크가 "새치기"하는 게 아니라, 그 파일 자체가 크기상
원천적으로 불가능하기 때문이다. test_huge_top_ranked_file_does_not_starve_the_rest는
이 D2 동작을 검증한다(과거에는 selected == ()가 "정상"이었으나 그건 의도치 않은
전멸이었다 -- shortlist.py의 D2 결정 주석 참고).
"""
from app.engines.codemap.models import RankedFile, RepoFile
from app.engines.codemap.shortlist import select_shortlist


def _rf(path, rank):
    return RankedFile(path=path, rank=rank, rank_score=1.0 - rank * 0.01, signals={}, rank_evidence={})


def _file(path, size):
    return RepoFile(path=path, ext=".py", size_bytes=size, line_count=1, text="x" * size)


def test_all_fit_when_budget_is_large():
    ranked = [_rf("a.py", 1), _rf("b.py", 2), _rf("c.py", 3)]
    files = {rf.path: _file(rf.path, 100) for rf in ranked}
    selected, truncated = select_shortlist(ranked, files, max_files=10, max_chars=10_000)
    assert selected == ("a.py", "b.py", "c.py")
    assert truncated == ()


def test_huge_top_ranked_file_does_not_starve_the_rest():
    """ D2: 1등 파일 혼자서 예산 전체보다 크면, 그 파일만 건너뛰고 2등 이하는
    정상적으로 채워진다 -- 예산 밖으로 밀리는 게 "새치기"가 아니라 그 파일이
    원천적으로 못 들어갈 크기이기 때문이다. """
    ranked = [_rf("huge.py", 1), _rf("small1.py", 2), _rf("small2.py", 3)]
    files = {"huge.py": _file("huge.py", 20_000), "small1.py": _file("small1.py", 10), "small2.py": _file("small2.py", 10)}
    selected, truncated = select_shortlist(ranked, files, max_files=10, max_chars=12_000)
    assert selected == ("small1.py", "small2.py")
    assert truncated == ("huge.py",)


def test_huge_file_in_the_middle_of_the_ranking_is_skipped_not_stopped():
    """ D2가 1등에만 특별한 게 아니라 "이 파일 자체가 예산보다 큰가"만 보는지
    확인 -- 2등이 거대해도 1등·3등은 정상적으로 채워진다. """
    ranked = [_rf("a.py", 1), _rf("huge.py", 2), _rf("c.py", 3)]
    files = {"a.py": _file("a.py", 10), "huge.py": _file("huge.py", 20_000), "c.py": _file("c.py", 10)}
    selected, truncated = select_shortlist(ranked, files, max_files=10, max_chars=12_000)
    assert selected == ("a.py", "c.py")
    assert truncated == ("huge.py",)


def test_stops_exactly_where_budget_runs_out():
    ranked = [_rf("a.py", 1), _rf("b.py", 2), _rf("c.py", 3)]
    files = {"a.py": _file("a.py", 5_000), "b.py": _file("b.py", 5_000), "c.py": _file("c.py", 5_000)}
    selected, truncated = select_shortlist(ranked, files, max_files=10, max_chars=12_000)
    assert selected == ("a.py", "b.py")
    assert truncated == ("c.py",)


def test_normal_budget_exhaustion_still_stops_a_later_small_file_too():
    """ D2는 "파일 자체가 예산보다 큰" 경우만 skip이다 -- 누적 소진으로 밀리는
    평범한 경우는 D1 그대로 멈춘다. c.py(5000자, 그 자체는 예산보다 작음)에서
    누적 예산이 소진되면, 훨씬 작은 d.py(10자)조차 새치기해서 들어오지 않는다. """
    ranked = [_rf("a.py", 1), _rf("b.py", 2), _rf("c.py", 3), _rf("d.py", 4)]
    files = {
        "a.py": _file("a.py", 5_000), "b.py": _file("b.py", 5_000),
        "c.py": _file("c.py", 5_000), "d.py": _file("d.py", 10),
    }
    selected, truncated = select_shortlist(ranked, files, max_files=10, max_chars=12_000)
    assert selected == ("a.py", "b.py")
    assert truncated == ("c.py", "d.py")


def test_max_files_limit_is_respected():
    ranked = [_rf(f"f{i}.py", i) for i in range(5)]
    files = {rf.path: _file(rf.path, 10) for rf in ranked}
    selected, truncated = select_shortlist(ranked, files, max_files=2, max_chars=10_000)
    assert selected == ("f0.py", "f1.py")
    assert truncated == ("f2.py", "f3.py", "f4.py")


def test_empty_ranked_list():
    selected, truncated = select_shortlist([], {}, max_files=10, max_chars=1000)
    assert selected == ()
    assert truncated == ()
