""" scan_directory()의 입력 규모 상한 회귀 테스트 (2026-08-04, redteam audit H13).

vendor 스캐너(우리 소유 아님)의 O(J²) 비용을 rules.py 쪽에서 미리 캡한다 -- 파일
개수와 합산 바이트를 각각 확인(둘 중 하나만 캡하면 다른 경로로 여전히 뚫린다).
"""
from pathlib import Path

import pytest

from app.engines.analysis import rules


def test_file_count_over_the_limit_is_rejected(tmp_path):
    for i in range(rules.MAX_SCAN_FILE_COUNT + 1):
        (tmp_path / f"f{i}.py").write_text("x = 1\n")

    with pytest.raises(ValueError, match="파일 수가 상한"):
        rules._enforce_scan_limits(str(tmp_path))


def test_file_count_at_the_limit_is_accepted(tmp_path):
    for i in range(rules.MAX_SCAN_FILE_COUNT):
        (tmp_path / f"f{i}.py").write_text("x = 1\n")

    rules._enforce_scan_limits(str(tmp_path))  # raises나 안 나면 통과


def test_total_bytes_over_the_limit_is_rejected_even_with_few_files(tmp_path):
    """파일 개수는 적어도(캡 훨씬 아래) 합산 용량이 크면 여전히 막힌다 --
    개수 캡 하나만으론 못 막는 경로."""
    big = "x" * (rules.MAX_SCAN_TOTAL_BYTES + 1)
    (tmp_path / "one_huge_file.py").write_text(big)

    with pytest.raises(ValueError, match="총 용량이 상한"):
        rules._enforce_scan_limits(str(tmp_path))


def test_small_submission_passes_both_checks(tmp_path):
    (tmp_path / "a.py").write_text("def a():\n    pass\n")
    (tmp_path / "b.py").write_text("def b():\n    pass\n")

    rules._enforce_scan_limits(str(tmp_path))


def test_scan_directory_rejects_oversized_submission_before_calling_vendor(tmp_path):
    """scan_directory() 진입점 자체가 vendor 호출 전에 막는지 end-to-end로 확인."""
    for i in range(rules.MAX_SCAN_FILE_COUNT + 1):
        (tmp_path / f"f{i}.py").write_text("x = 1\n")

    with pytest.raises(ValueError, match="파일 수가 상한"):
        rules.scan_directory(str(tmp_path))
