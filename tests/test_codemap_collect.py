""" app/engines/codemap/collect.py -- 유일한 파일시스템 I/O 지점의 테스트

D12: .git 스킵, node_modules/dist 스킵, 생성 파일명 패턴 스킵, 바이너리 스킵,
심볼릭 링크 탈출 거부, (당연히) subprocess/exec 호출이 전혀 없음을 확인한다.
"""
import os

from app.engines.codemap.collect import collect_repo_files
from app.engines.codemap.models import CollectLimits


def _write(root, relpath, content=""):
    full = root / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        full.write_bytes(content)
    else:
        full.write_text(content, encoding="utf-8")
    return full


def test_skips_git_dir(tmp_path):
    _write(tmp_path, "src/app.py", "print(1)\n")
    _write(tmp_path, ".git/HEAD", "ref: refs/heads/main\n")
    files = collect_repo_files(str(tmp_path))
    assert {f.path for f in files} == {"src/app.py"}


def test_skips_node_modules_and_dist(tmp_path):
    _write(tmp_path, "src/index.ts", "export const x = 1;\n")
    _write(tmp_path, "node_modules/left-pad/index.js", "module.exports = 1;\n")
    _write(tmp_path, "dist/bundle.js", "console.log(1);\n")
    files = collect_repo_files(str(tmp_path))
    assert {f.path for f in files} == {"src/index.ts"}


def test_skips_minified_and_contenthash_files(tmp_path):
    _write(tmp_path, "public/main.a1b2c3d4e5.js", "/* bundle */\n")
    _write(tmp_path, "public/lib.min.js", "/* min */\n")
    _write(tmp_path, "src/real.js", "export const x = 1;\n")
    files = collect_repo_files(str(tmp_path))
    assert {f.path for f in files} == {"src/real.js"}


def test_skips_binaries(tmp_path):
    _write(tmp_path, "assets/image.py", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x00\x00\x00")
    _write(tmp_path, "src/real.py", "print(1)\n")
    files = collect_repo_files(str(tmp_path))
    assert {f.path for f in files} == {"src/real.py"}


def test_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside_secret"
    outside.mkdir(exist_ok=True)
    (outside / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")

    repo = tmp_path / "repo"
    _write(repo, "src/real.py", "print(1)\n")
    os.symlink(outside, repo / "escaped")

    files = collect_repo_files(str(repo))
    assert {f.path for f in files} == {"src/real.py"}


def test_respects_max_file_bytes_limit(tmp_path):
    _write(tmp_path, "src/huge.py", "x = 1\n" * 100_000)
    _write(tmp_path, "src/small.py", "x = 1\n")
    files = collect_repo_files(str(tmp_path), limits=CollectLimits(max_file_bytes=1_000))
    assert {f.path for f in files} == {"src/small.py"}


def test_respects_max_total_files_limit(tmp_path):
    for i in range(10):
        _write(tmp_path, f"src/f{i}.py", "x = 1\n")
    files = collect_repo_files(str(tmp_path), limits=CollectLimits(max_total_files=3))
    assert len(files) == 3
