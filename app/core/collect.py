"""제출 소스 수집 — `shared/p02-engine.js`의 수집 규칙을 Python으로 이관.

원본 대응표 (p02-engine.js → 이 모듈):
- `SKIP_DIR_NAMES`, `SRC_EXTS`      → 동일 상수 (값 그대로)
- `isSkippedDir` / `isSkippedPath`  → `_is_skipped_dir` / `is_skipped_path`
                                      (D164: 확장자 비교는 소문자화 후 수행)
- `isNotebookPath`                  → `is_notebook_path`
- `extractNotebookSource`           → `extract_notebook_source`
                                      (D166: code 셀 source만 추출, 가상 `.py`로 제시)
- `parseZipFile`                    → `collect_from_zip`
                                      (JSZip 대신 zipfile + zip slip/용량 검증 추가)
- `fetchGithubRepo`                 → `collect_from_github`
                                      (GitHub REST API 대신 `git clone` — 커밋 귀속에
                                       `.git` 이력이 필요하므로, B5 결정과 정합)

수집 결과는 "가상 경로 → 소스 텍스트" 매핑이며, 목업이 Pyodide FS의 `/target`에
쓰던 것과 동일하게 작업공간 디렉터리에 그대로 materialize 한다.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# --- p02-engine.js와 값이 동일한 상수 (원본 68~69행) ---
SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        "__pycache__",
        ".venv",
        "venv",
        "static",
        "vendor",
        "vendored",
    }
)
SRC_EXTS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".py",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".cxx",
    ".hpp",
    ".swift",
)

# --- ZIP 안전 한계 ---
# 명세·B6 어디에도 확정 수치가 없다(§8 B2는 "대용량이면 공유 스토리지 재논의"까지만).
# 아래는 zip bomb·디스크 고갈을 막기 위한 잠정 방어값이며 백엔드 협의 대상이다.
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 해제 후 총 용량 상한 2GB
MAX_ZIP_ENTRIES = 200_000  # 엔트리 수 상한
MAX_SINGLE_FILE_BYTES = 8 * 1024 * 1024  # 소스 파일 1개 상한 8MB


class CollectError(Exception):
    """수집 실패 — `failure_reason` 코드를 함께 나른다."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class CollectedSource:
    """수집 결과. `files`는 상대경로 → 소스 텍스트."""

    files: dict[str, str] = field(default_factory=dict)
    notebook_code_count: int = 0
    skipped_ext_counts: dict[str, int] = field(default_factory=dict)
    # GITHUB_URL일 때만 채워진다 (§3.3: 재현·복원용 HEAD 고정값)
    commit_sha: str | None = None
    # clone/해제 산출물이 실제로 놓인 디렉터리 (`.git` 파싱·귀속에 사용)
    source_root: Path | None = None


def _is_skipped_dir(rel_path: str) -> bool:
    """원본 `isSkippedDir`: 경로 세그먼트 중 하나라도 SKIP_DIR_NAMES면 스킵."""
    return any(part in SKIP_DIR_NAMES for part in rel_path.split("/"))


def is_skipped_path(rel_path: str) -> bool:
    """원본 `isSkippedPath` (D164: 확장자 매칭은 대소문자 무시)."""
    if _is_skipped_dir(rel_path):
        return True
    ext = "." + (rel_path.split(".")[-1].lower() if "." in rel_path else "")
    return ext not in SRC_EXTS


def is_notebook_path(rel_path: str) -> bool:
    """원본 `isNotebookPath`."""
    return not _is_skipped_dir(rel_path) and rel_path.lower().endswith(".ipynb")


def extract_notebook_source(json_text: str) -> str | None:
    """원본 `extractNotebookSource` (D166).

    .ipynb는 JSON이라 소스 텍스트가 아니다 — code 셀의 `source`만 이어붙여
    가상 `.py`로 제시한다. markdown/raw 셀은 버린다. 파싱 실패 시 None.
    """
    try:
        nb = json.loads(json_text)
    except (ValueError, TypeError):
        return None  # malformed notebook JSON
    cells = nb.get("cells") if isinstance(nb, dict) else None
    if not isinstance(cells, list):
        cells = []
    parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        src = cell.get("source")
        text = "".join(src) if isinstance(src, list) else (src or "")
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _count_skip(counts: dict[str, int], rel_path: str) -> None:
    """원본 D153 진단 집계: 스킵된 확장자별 개수."""
    ext = "." + rel_path.split(".")[-1] if "." in rel_path else "(확장자 없음)"
    counts[ext] = counts.get(ext, 0) + 1


def _add_entry(result: CollectedSource, rel_path: str, read_text) -> None:
    """원본 parseZipFile 루프 본문과 동일한 분기 (notebook → skip → 일반 소스)."""
    if is_notebook_path(rel_path):
        try:
            raw = read_text()
        except Exception:
            result.skipped_ext_counts[".ipynb(읽기실패)"] = (
                result.skipped_ext_counts.get(".ipynb(읽기실패)", 0) + 1
            )
            return
        src = extract_notebook_source(raw)
        if src and src.strip():
            # 원본과 동일하게 원 경로에 ".py"를 덧붙인 가상 파일명을 쓴다.
            result.files[rel_path + ".py"] = src
            result.notebook_code_count += 1
        else:
            key = ".ipynb(코드셀 없음/파싱실패)"
            result.skipped_ext_counts[key] = result.skipped_ext_counts.get(key, 0) + 1
        return

    if is_skipped_path(rel_path):
        _count_skip(result.skipped_ext_counts, rel_path)
        return

    try:
        result.files[rel_path] = read_text()
    except Exception:
        # 원본 주석 그대로: binary file, skip
        pass


def _safe_member_path(name: str) -> str | None:
    """zip slip 방어: 절대경로·드라이브·상위 참조(..)를 가진 멤버를 거부한다.

    JSZip은 브라우저 가상 FS에만 썼기에 원본에는 이 검증이 없었다. 서버는 실제
    파일시스템에 해제하므로 필수로 추가한 항목이다.
    """
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/")[0]:
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) if parts else None


def collect_from_zip(zip_bytes: bytes, workspace: Path) -> CollectedSource:
    """ZIP 해제 + 소스 수집 (원본 `parseZipFile` 이관 + 안전 검증).

    `.git`·`commits.txt`·`changed_files.txt` 등 귀속 판정에 필요한 비소스 파일도
    디스크에는 풀어둔다(수집 대상 `files`에는 들어가지 않는다) — B5의 ZIP 경로가
    이 파일들을 읽어야 하기 때문이다.
    """
    extract_root = workspace / "extracted"
    extract_root.mkdir(parents=True, exist_ok=True)

    result = CollectedSource(source_root=extract_root)
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise CollectError("FETCH_FAILED", f"ZIP 압축 해제 실패: {exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_ENTRIES:
            raise CollectError(
                "FETCH_FAILED", f"ZIP 엔트리 수 상한({MAX_ZIP_ENTRIES}) 초과"
            )
        total = sum(i.file_size for i in infos)
        if total > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise CollectError(
                "FETCH_FAILED",
                f"ZIP 해제 용량 상한({MAX_ZIP_UNCOMPRESSED_BYTES} bytes) 초과",
            )

        for info in infos:
            rel = _safe_member_path(info.filename)
            if rel is None:
                continue  # zip slip 시도 — 조용히 제외
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                _count_skip(result.skipped_ext_counts, rel)
                continue
            dest = extract_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
            _add_entry(result, rel, lambda d=dest: d.read_text(encoding="utf-8"))

    return result


def collect_from_github(
    repo_url: str, branch: str | None, workspace: Path, timeout_sec: int = 300
) -> CollectedSource:
    """공개 레포 clone 후 소스 수집 (B5: PAT 없음).

    원본은 GitHub REST API로 blob을 하나씩 받았다(p02-engine.js `fetchGithubRepo`).
    서버에서는 `git clone`으로 바꿨다 — 이유 두 가지:
      1. 커밋 귀속(OWN_COMMIT)에 `.git` 이력이 필요하다. REST tree API에는 없다.
      2. 원본이 D192로 겪은 비인증 rate limit(IP당 60회/시간)을 clone은 받지 않는다.

    **`--depth`를 쓰지 않는다**: 얕은 클론은 이력이 잘려 author별 커밋·변경 파일
    추출이 불완전해진다(귀속이 조용히 틀린 결과를 내는 것보다 느린 편이 낫다).
    """
    clone_dir = workspace / "repo"
    cmd = ["git", "clone", "--quiet"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [repo_url, str(clone_dir)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec, check=False
        )
    except FileNotFoundError as exc:
        raise CollectError("FETCH_FAILED", "git 실행 파일을 찾을 수 없습니다") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectError(
            "FETCH_FAILED", f"git clone 타임아웃({timeout_sec}s)", retryable=True
        ) from exc

    if proc.returncode != 0:
        # 공개 레포 전용이므로 인증 실패도 여기로 떨어진다(비공개·오타 구분 불가).
        raise CollectError(
            "FETCH_FAILED",
            f"git clone 실패: {(proc.stderr or '').strip()[:500]}",
            retryable=True,
        )

    result = CollectedSource(source_root=clone_dir)
    result.commit_sha = _git_head_sha(clone_dir)

    for path in sorted(clone_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(clone_dir).as_posix()
        _add_entry(result, rel, lambda p=path: p.read_text(encoding="utf-8"))

    return result


def _git_head_sha(repo_dir: Path) -> str | None:
    """§3.3: 분석 시점 HEAD 고정값 — 재분석·원문 재확보의 근거."""
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def materialize(files: dict[str, str], target_root: Path) -> None:
    """수집된 파일들을 스캔 대상 디렉터리에 기록한다.

    목업의 `writeTargetFiles()`가 Pyodide FS `/target`에 하던 일과 동일하다.
    파이프라인이 실제 파일시스템을 walk하므로 반드시 물리 파일이어야 한다.
    """
    if target_root.exists():
        shutil.rmtree(target_root, ignore_errors=True)
    target_root.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        dest = target_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
