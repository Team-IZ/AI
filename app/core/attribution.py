"""커밋 귀속 (extraction_scope=OWN_COMMIT) — 명세 B5 "커밋 로그 확인 방법".

- **GITHUB_URL**: clone된 `.git`을 `git log`로 파싱해 author email별 변경 파일을 뽑는다.
- **ZIP**: `.git`이 있으면 동일하게 파싱. 없으면 동봉된 `commits.txt`/`changed_files.txt`
  export를 읽는다. 둘 다 없으면 귀속 불가 → 호출자가 TOTAL 폴백(MEAS-02A A-2).

⚠️ 미확정: `commits.txt`/`changed_files.txt`의 **정확한 포맷이 명세에 없다**
(B5는 "git log export 파일 2개를 동봉"까지만 규정하고, 제출 페이지의 OS별 명령어도
아직 없다). 아래 파서는 흔한 `git log` 출력들을 관용적으로 받아들이도록 썼고,
포맷이 확정되면 이 모듈만 좁히면 된다.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# `git log --name-only`를 한 번에 파싱하기 위한 커밋 구분자
_SEP = "\x1e__COMMIT__"
_LOG_FORMAT = f"{_SEP}%H%x1f%ae"

# 원문 `git log` 기본 출력의 Author 줄에서 이메일을 뽑는 폴백 패턴
_AUTHOR_LINE = re.compile(r"^Author:\s*.*<([^>]+)>", re.MULTILINE)
_COMMIT_LINE = re.compile(r"^commit\s+([0-9a-f]{7,40})", re.MULTILINE)


@dataclass
class Attribution:
    """§3.2 `result.attribution` 대응."""

    attributed_files: list[str] = field(default_factory=list)
    commit_count: int = 0
    # AUTH-07: 분석 시점 스냅샷. `.git` 실물에서 뽑았으면 VERIFIED,
    # 교육생이 동봉한 텍스트 export에 의존했으면 위변조 검증이 불가하므로 UNVERIFIED.
    verification_status: str = "UNVERIFIED"


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def from_git_repo(repo_dir: Path, commit_email: str) -> Attribution | None:
    """`.git` 이력에서 본인 커밋·변경 파일을 추출한다.

    `.git`이 없거나 git 호출이 실패하면 None (호출자가 다음 수단으로 넘어간다).

    `--no-merges`를 쓴다: 머지 커밋은 본인이 작성한 코드가 아니고, `--name-only`도
    머지에 대해서는 기본적으로 아무 경로를 내지 않는다. **결과적으로 머지 커밋만
    가진 제출자는 귀속 0건(→ ATTRIBUTION_REQUIRED)이 된다** — 실측 확인됨
    (octocat/Hello-World의 octocat 계정). 의도된 동작이나 백엔드 확인 대상이다.

    이메일 비교는 대소문자를 무시한다(`_normalize`).
    """
    if not (repo_dir / ".git").exists():
        return None

    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "log", f"--pretty=format:{_LOG_FORMAT}",
         "--name-only", "--no-merges"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return None

    target = _normalize(commit_email)
    files: set[str] = set()
    commit_count = 0

    for block in proc.stdout.split(_SEP):
        block = block.strip("\n")
        if not block:
            continue
        header, _, body = block.partition("\n")
        parts = header.split("\x1f")
        if len(parts) < 2:
            continue
        if _normalize(parts[1]) != target:
            continue
        commit_count += 1
        for line in body.splitlines():
            line = line.strip()
            if line:
                files.add(line.replace("\\", "/"))

    return Attribution(
        attributed_files=sorted(files),
        commit_count=commit_count,
        verification_status="VERIFIED",
    )


def from_export_files(root: Path, commit_email: str) -> Attribution | None:
    """ZIP에 동봉된 `commits.txt` / `changed_files.txt` export를 파싱한다.

    둘 다 없으면 None. `changed_files.txt`가 없고 `commits.txt`만 있으면
    커밋 수는 세되 파일 귀속은 비게 되므로 호출자가 폴백으로 처리한다.
    """
    commits_path = _find(root, "commits.txt")
    changed_path = _find(root, "changed_files.txt")
    if commits_path is None and changed_path is None:
        return None

    target = _normalize(commit_email)
    commit_count = 0
    files: set[str] = set()

    if commits_path is not None:
        text = commits_path.read_text(encoding="utf-8", errors="replace")
        commit_count = _count_commits(text, target)

    if changed_path is not None:
        files = _parse_changed_files(
            changed_path.read_text(encoding="utf-8", errors="replace"), target
        )

    return Attribution(
        attributed_files=sorted(files),
        commit_count=commit_count,
        # 교육생이 만든 텍스트 파일이므로 위변조를 서버가 검증할 수 없다.
        verification_status="UNVERIFIED",
    )


def _find(root: Path, name: str) -> Path | None:
    """루트 바로 아래 또는 (ZIP이 한 겹 감싼 흔한 경우) 한 단계 안까지 찾는다."""
    direct = root / name
    if direct.is_file():
        return direct
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if child.is_dir():
            candidate = child / name
            if candidate.is_file():
                return candidate
    return None


def _count_commits(text: str, target_email: str) -> int:
    """`commits.txt`에서 본인 커밋 수를 센다 (포맷 관용 파싱).

    지원 형태:
      1. 구분자 포맷 — `<sha>|<email>|...` 처럼 이메일이 필드로 들어간 경우
      2. `git log` 기본 출력 — `commit <sha>` + `Author: 이름 <email>` 블록
    """
    emails = [_normalize(e) for e in _AUTHOR_LINE.findall(text)]
    if emails:  # 형태 2
        return sum(1 for e in emails if e == target_email)

    count = 0
    for line in text.splitlines():  # 형태 1
        line = line.strip()
        if not line:
            continue
        fields = [f.strip().lower() for f in re.split(r"[|\t;,]", line)]
        if target_email in fields:
            count += 1
    return count


def _parse_changed_files(text: str, target_email: str) -> set[str]:
    """`changed_files.txt`에서 본인 커밋의 변경 파일 경로를 뽑는다.

    지원 형태:
      1. 커밋 헤더(이메일 포함) + 그 아래 파일 경로 목록이 반복되는 `--name-only` 출력
      2. 이메일이 전혀 없는 순수 경로 목록 — 이미 본인 것만 export했다고 보고 전부 채택
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    has_email_marker = any("@" in ln for ln in lines)

    if not has_email_marker:  # 형태 2
        return {
            ln.strip().replace("\\", "/")
            for ln in lines
            if ln.strip() and not ln.startswith("#")
        }

    files: set[str] = set()
    current_is_target = False
    for line in lines:  # 형태 1
        stripped = line.strip()
        if not stripped:
            continue
        if "@" in stripped:
            fields = [f.strip().lower() for f in re.split(r"[|\t;,<>\s]+", stripped)]
            current_is_target = target_email in fields
            continue
        if current_is_target:
            files.add(stripped.replace("\\", "/"))
    return files
