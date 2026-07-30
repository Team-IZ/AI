""" 저장소에서 소스 파일을 수집한다 -- 유일한 파일시스템 I/O 지점(불순 edge)

D12 (2026-07-30): 학생이 제출한 코드는 읽기만 한다 -- 실행하지 않고, .git은 LLM
단계에 노출하지 않고(SKIP_DIRS에 이미 포함), 심볼릭 링크로 저장소 밖을 가리키는
탈출 시도를 거부한다. 이 파일이 subprocess/exec/eval을 호출하는 일은 절대 없다.

SKIP_DIRS/GENERATED_FILENAME_RE 등은 cognition/two_tier_scan.py(origin/feat/
poc_full)의 D76/D195가 실측 근거로 확장해 온 목록을 그대로 가져온다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from app.engines.codemap.models import CollectLimits, RepoFile

SRC_EXTS = (
    ".ts", ".tsx", ".js", ".jsx",
    ".py",
    ".java",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
)

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv",
    "static", "vendor", "vendored",
    "target",
    ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
    ".next", ".nuxt", ".output", ".nitro", ".svelte-kit", ".turbo", ".parcel-cache",
    "coverage", "storybook-static",
    ".vs",
    "DerivedData",
}
SKIP_DIR_PREFIXES = ("cmake-build-",)
SKIP_DIR_SUFFIXES = (".egg-info",)

GENERATED_FILENAME_RE = re.compile(r"\.min\.jsx?$|\.[0-9a-f]{8,20}\.jsx?$|\.d\.ts$", re.I)


def _is_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(SKIP_DIR_PREFIXES) or name.endswith(SKIP_DIR_SUFFIXES)


def collect_repo_files(repo_dir: str, *, limits: CollectLimits | None = None) -> tuple[RepoFile, ...]:
    """ repo_dir 아래를 걸어 소스 파일만 읽어 RepoFile 튜플로 반환

    - 심볼릭 링크가 repo_dir 밖을 가리키면 거부(탈출 방지)
    - limits.max_file_bytes보다 크면 스킵(생성물/자산일 가능성 + 메모리 보호)
    - UTF-8로 디코드 안 되면(바이너리) 스킵
    - 절대 파일을 실행/서브프로세스로 넘기지 않는다 -- read_text뿐
    """
    limits = limits or CollectLimits()
    root = Path(repo_dir).resolve()
    results: list[RepoFile] = []

    for dirpath, dirnames, filenames in _walk(root):
        dirnames[:] = [d for d in dirnames if not _is_skip_dir(d)]
        for filename in filenames:
            if len(results) >= limits.max_total_files:
                return tuple(results)
            if not filename.endswith(SRC_EXTS) or GENERATED_FILENAME_RE.search(filename):
                continue

            full = dirpath / filename
            try:
                resolved = full.resolve()
            except OSError:
                continue
            if not _is_within(resolved, root):
                continue  # 심볼릭 링크 탈출 시도 -- 조용히 제외

            try:
                size = resolved.stat().st_size
            except OSError:
                continue
            if size > limits.max_file_bytes:
                continue

            try:
                text = resolved.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue  # 바이너리 파일 -- D12: 실행은커녕 텍스트로도 다루지 않는다

            relpath = str(full.relative_to(root)).replace("\\", "/")
            ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
            results.append(RepoFile(path=relpath, ext=ext, size_bytes=size, line_count=text.count("\n") + 1, text=text))

    return tuple(results)


def _walk(root: Path):
    """ os.walk과 동등하되 pathlib 기반 -- (dirpath, dirnames, filenames) 3-튜플 생성 """
    stack = [root]
    while stack:
        current = stack.pop()
        dirnames: list[str] = []
        filenames: list[str] = []
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() and entry.is_dir():
                continue  # 디렉터리 심볼릭 링크는 애초에 내려가지 않는다(탈출/순환 방지)
            if entry.is_dir():
                dirnames.append(entry.name)
            elif entry.is_file():
                filenames.append(entry.name)
        yield current, dirnames, filenames
        for d in dirnames:
            stack.append(current / d)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
