""" GITHUB_URL 클론 경로의 심볼릭 링크 방어 회귀 테스트 (2026-08-04, redteam 감사 C3).

버그: ZIP 추출 경로(rules._safe_extract)는 심볼릭 링크 항목을 스킵하지만, GITHUB_URL
git-clone 경로(materialize.py)는 클론 결과를 검사 없이 그대로 넘겼다. 두 경로가 합류하는
rules.scan_directory()에서 vendor 스캐너를 부르기 전에 링크를 지워야 두 경로가 동치가
된다(rules.scan_directory의 docstring이 명시하는 요구사항).
"""
import os

import pytest

from app.engines.analysis import rules


def test_strip_symlinks_removes_file_symlink_pointing_outside_root(tmp_path):
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("NVIDIA_API_KEY_1=super-secret\n")

    root = tmp_path / "root"
    root.mkdir()
    (root / "real.py").write_text("def real():\n    return 1\n")
    os.symlink(outside, root / "leak.py")

    rules._strip_symlinks(str(root))

    assert not (root / "leak.py").exists()
    assert not (root / "leak.py").is_symlink()
    assert (root / "real.py").read_text() == "def real():\n    return 1\n"
    # 링크 대상 자체는 root 밖이므로 건드리면 안 된다.
    assert outside.exists()


def test_strip_symlinks_removes_dir_symlink_without_touching_its_target(tmp_path):
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    sentinel = outside_dir / "sentinel.txt"
    sentinel.write_text("do not touch")

    root = tmp_path / "root"
    root.mkdir()
    os.symlink(outside_dir, root / "linked_dir")

    rules._strip_symlinks(str(root))

    assert not (root / "linked_dir").exists()
    assert not os.path.islink(str(root / "linked_dir"))
    # 링크로 걸려 있던 디렉터리 자체와 그 내용물은 root 밖이므로 그대로 남아 있어야 한다
    # (naive한 rglob+unlink였다면 중간 경로가 링크를 타고 나가 이걸 지웠을 수 있다).
    assert sentinel.exists()
    assert sentinel.read_text() == "do not touch"


def test_strip_symlinks_preserves_real_nested_files(tmp_path):
    root = tmp_path / "root"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n")

    rules._strip_symlinks(str(root))

    assert (root / "pkg" / "mod.py").read_text() == "x = 1\n"


def test_scan_directory_does_not_leak_symlinked_file_content(tmp_path):
    """clone 경로를 흉내낸 디렉터리(심볼릭 링크 포함)를 scan_directory에 직접 넘겨서
    실제 파이프라인(스캔+파일수집)까지 통과했을 때 링크 콘텐츠가 새지 않는지 확인한다."""
    outside = tmp_path / "outside_secret.py"
    outside.write_text("NVIDIA_API_KEY_1 = 'super-secret-value'\n")

    root = tmp_path / "cloned_repo"
    root.mkdir()
    (root / "App.py").write_text("def main():\n    return 1\n")
    os.symlink(outside, root / "leak.py")

    result = rules.scan_directory(str(root))

    assert "leak.py" not in result["files"]
    assert all("super-secret-value" not in text for text in result["files"].values())
    assert "App.py" in result["files"]
