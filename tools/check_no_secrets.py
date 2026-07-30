#!/usr/bin/env python3
""" D9 가드 -- NVIDIA API 키가 절대 커밋되지 않게 하는 전용 검사

D9 (2026-07-30): code-importance-map 스테이지의 실배포는 40 rpm 단일 키를 쓴다
(NvidiaKeyPool 로테이션은 사용자가 로컬 테스트 편의로 만든 것일 뿐, 실배포 코드에는
들어가지 않는다). 이 키는 로컬에서만 필요할 때 읽고, 절대 커밋/push되지 않아야
한다 -- 그 "절대"를 사람의 주의력이 아니라 이 스크립트가 강제한다.

汎용 시크릿 스캐너(예: gitleaks 전체 룰셋)를 새로 들이는 대신, 이 프로젝트가
실제로 다루는 것(NVIDIA API 키, 그리고 그 로컬 전용 키풀 파일)만 정확히 노린다 --
범위를 좁혀야 무엇을 왜 막는지 이 파일 하나만 읽고 알 수 있다.

두 모드:
  --staged   git diff --cached (커밋 직전, pre-commit 훅용)
  --tracked  git ls-files (이미 커밋된 것도 검사, CI용 -- 훅을 건너뛴 커밋을 잡는다)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# 이 파일과 테스트 파일은 패턴을 "데이터"로 담고 있으므로 스캔 대상에서 제외한다.
SELF_EXCLUDE = {"tools/check_no_secrets.py", "tests/test_secret_guard.py"}

# --- 파일명 규칙 -----------------------------------------------------------

BANNED_FILENAME_PATTERNS = [
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.[^/]+$"),
    re.compile(r"\.env$"),
    re.compile(r"(^|/)scripts/local/"),
    re.compile(r"(^|/)nvidia_key_pool\.py$"),
    re.compile(r"(?i)apikey"),
    re.compile(r"(?i)api_key.*\.json$"),
]
ALLOWED_FILENAME_EXCEPTIONS = {".env.example"}

# --- 내용 규칙 --------------------------------------------------------------

CONTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("NVAPI_KEY_LITERAL", re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}")),
    ("NVIDIA_API_KEY_ASSIGNMENT", re.compile(r"NVIDIA_API_KEY\s*=\s*['\"]?[^\s'\"#]{10,}")),
    ("NVIDIA_KEYPOOL_NAMING", re.compile(r"NVIDIA_API_KEY_\d+\s*=\s*\S")),
]


def _is_banned_filename(relpath: str) -> bool:
    basename = relpath.rsplit("/", 1)[-1]
    if basename in ALLOWED_FILENAME_EXCEPTIONS:
        return False
    return any(p.search(relpath) for p in BANNED_FILENAME_PATTERNS)


def _content_violations(relpath: str, text: str) -> list[str]:
    violations = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule_name, pattern in CONTENT_PATTERNS:
            if pattern.search(line):
                violations.append(f"{relpath}:{line_no}  {rule_name}")
    return violations


def _git_staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _git_tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def check(relpaths: list[str], repo_root: Path) -> list[str]:
    findings: list[str] = []
    for relpath in relpaths:
        if relpath in SELF_EXCLUDE:
            continue
        if _is_banned_filename(relpath):
            findings.append(f"{relpath}  BANNED_FILENAME")
            continue  # 파일명 자체가 걸리면 내용은 더 볼 필요 없음(바이너리일 수도 있음)

        full = repo_root / relpath
        if not full.is_file():
            continue  # 삭제된 파일 등
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 바이너리 -- 이 가드의 대상이 아님

        if relpath.endswith(".env.example"):
            # .env.example은 키 이름은 있어도 값이 비어 있어야 통과
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("NVIDIA_API_KEY") and "=" in stripped:
                    _, _, rhs = stripped.partition("=")
                    if rhs.strip():
                        findings.append(f"{relpath}  ENV_EXAMPLE_HAS_REAL_VALUE")
            continue

        findings.extend(_content_violations(relpath, text))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="git diff --cached 대상만 검사")
    group.add_argument("--tracked", action="store_true", help="git ls-files 전체를 검사")
    args = parser.parse_args(argv)

    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    )

    relpaths = _git_staged_files() if args.staged else _git_tracked_files()
    findings = check(relpaths, repo_root)

    for f in findings:
        print(f)
    if findings:
        print(f"FAIL: {len(findings)} secret-guard violation(s)", file=sys.stderr)
        return 1
    print("OK: no secret-guard violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
