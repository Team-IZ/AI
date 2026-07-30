""" app/engines/codemap/shortlist.py -- 예산 안에서 랭크 순서로 채우는 로직 테스트

D1(원본 버그): buildCodeBlock()의 알파벳순 그리디 채우기가 첫 큰 파일에 예산을
다 뺏겨 78개 중 1개만 살아남았다. 여기서는 랭크 순서를 지키고, 예산을 넘기는
지점부터는 새치기 없이 전부 truncated로 넘긴다는 것만 검증한다(순서 자체가
옳은지는 rank.py의 책임 -- 이 모듈은 "이미 정해진 순서를 예산 안에서 어떻게
채우는가"만 담당한다).
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
    """ 1등 파일이 예산 전체를 넘기면, 그 파일부터 전부 truncated로 밀린다 --
    2등 이하가 새치기해서 들어오지 않는다(순서 신뢰성이 우선). """
    ranked = [_rf("huge.py", 1), _rf("small1.py", 2), _rf("small2.py", 3)]
    files = {"huge.py": _file("huge.py", 20_000), "small1.py": _file("small1.py", 10), "small2.py": _file("small2.py", 10)}
    selected, truncated = select_shortlist(ranked, files, max_files=10, max_chars=12_000)
    assert selected == ()
    assert truncated == ("huge.py", "small1.py", "small2.py")


def test_stops_exactly_where_budget_runs_out():
    ranked = [_rf("a.py", 1), _rf("b.py", 2), _rf("c.py", 3)]
    files = {"a.py": _file("a.py", 5_000), "b.py": _file("b.py", 5_000), "c.py": _file("c.py", 5_000)}
    selected, truncated = select_shortlist(ranked, files, max_files=10, max_chars=12_000)
    assert selected == ("a.py", "b.py")
    assert truncated == ("c.py",)


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
