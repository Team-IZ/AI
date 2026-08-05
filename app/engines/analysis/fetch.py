""" 분석 입력 fetch — `POST /analysis-inputs` 전용. `materialize.py`의 형제 모듈.

백엔드 제안(`api-request-to-ai-server.md`, 2026-08-05)에 따라 "검증+fetch"를 "분석 실행"에서
떼어내는 신규 경로다. 세 가지 결정을 전제로 한다(이 세션에서 이미 확정, 여기서 재검토하지 않음):

  D1  git 히스토리 수집은 커밋 개수가 아니라 **벽시계 시간**으로 상한. 코드 자체를 가져오는
      것(Phase A, 필수)과 히스토리로 풍부화하는 것(Phase B, best-effort)을 분리한다 —
      Phase B가 느리거나 실패해도 Phase A 결과는 절대 버리지 않는다.
  D2  fetch한 코드를 서버가 캐싱하지 않는다. `POST /analysis`는 이 모듈의 `refetch_pinned()`로
      **재fetch**한다(GITHUB_URL은 정확한 커밋 sha로, ZIP은 같은 다운로드 URL로) — `/sessions`를
      무상태로 다시 설계했던 것과 같은 이유(재배포·멀티인스턴스에서 인메모리 상태가 못 버틴다).
  D3  ZIP의 git 히스토리는 ①백엔드가 요청에 실어 보내면 그것 우선 ②ZIP 안에 `.git`이 있으면
      직접 파싱 ③둘 다 없으면 실패시키지 않고 빈 값으로 진행(`ZIP_REQUIRE_GIT_LOG`로 정책 전환 가능).

`materialize.py`의 D12 방어(`validate_repo_url`/`validate_branch`/`git_env`)를 그대로 재사용한다
— 복사하면 한쪽만 패치되고 다른 쪽이 낡는다(이 세션에서 vendor drift로 실제 겪은 사고와 같은 클래스).
"""
from __future__ import annotations

import datetime
import hashlib
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.engines.analysis import materialize, rules

# git log 파싱용 구분자. ASCII 제어문자라 커밋 메시지·작성자명에 우연히 섞일 가능성이
# 극히 낮다 -- 완벽한 injection-proof 파서는 아니지만(학생이 의도적으로 이 바이트를
# 커밋 메시지에 넣으면 그 한 커밋의 필드가 깨질 수 있다), 여기는 텍스트 추출일 뿐이라
# 최악의 경우도 "그 커밋 메타데이터가 잘못 파싱됨"이지 실행/보안 문제로 안 이어진다.
# (진짜 보안 경계는 임베디드 .git 실행 방어 쪽 -- 아래 D-git-rce 참조.)
_RS = "\x1e"
_FS = "\x1f"

_ANALYSIS_INPUT_NAMESPACE = uuid.UUID("6f1b1a9e-6b3a-4a1a-9b0a-2f6d1c9a7e01")


@dataclass(frozen=True)
class FetchedInput:
    """fetch() 한 번의 결과. `/analysis-inputs` 응답과 `/analysis` 재fetch 검증 둘 다의 기반."""

    root: str
    method: str
    resolved_branch: str | None
    head_commit: dict[str, Any] | None
    git_history: list[dict[str, Any]] = field(default_factory=list)
    git_history_source: str = "NONE"  # BACKEND_SUPPLIED / EMBEDDED_GIT / REMOTE_DEEPEN / NONE
    history_truncated: bool = False
    input_hash: str = ""
    file_count: int = 0
    byte_count: int = 0

    def __fspath__(self) -> str:
        return self.root


# `api-request-to-ai-server.md`가 요청한 11개 failureCode. `/analysis-inputs`(fetch())가
# 내는 값은 항상 이 집합 안에 있어야 한다 -- 백엔드 DB CHECK 제약이 이 문자열 그대로다.
# 한 곳에 모아두는 이유: 이번 세션에서 겪은 feature_code 사례처럼, 실제 배포본과
# 대조해서 하나라도 바뀌면 여기 값-집합 하나만 고치면 되게 하기 위해서다.
GITHUB_FAILURE_CODES = frozenset({
    "INVALID_REPOSITORY_URL", "REPO_NOT_FOUND", "REPOSITORY_ACCESS_DENIED",
    "BRANCH_NOT_FOUND", "UNSUPPORTED_HOST", "TEMPORARY_ERROR",
})
ZIP_FAILURE_CODES = frozenset({
    "FILE_TOO_LARGE", "ARCHIVE_INVALID", "EMPTY_CODE", "PROHIBITED_FILE", "GIT_LOG_MISSING",
})
VERIFICATION_FAILURE_CODES = GITHUB_FAILURE_CODES | ZIP_FAILURE_CODES

# `refetch_pinned()`(D2, `/analysis` 잡 처리 중 호출) 전용 추가 코드. 위 11개는
# `repository_verification`/`submission_artifact`용이고, 이 둘은 아직 정의 안 된
# `analysis_job.failure_code`용 잠정값이다(계획 §0.3 -- 백엔드 확인 필요).
JOB_ONLY_FAILURE_CODES = frozenset({"INPUT_HASH_MISMATCH", "FETCH_FAILED"})


class FetchError(Exception):
    """failureCode 하나로 분류된 fetch 실패. `/analysis-inputs`는 그대로 422가 되고,

    `refetch_pinned()`가 내는 것은 job의 `failure_code`가 된다(위 두 벌 중 하나에서 옴).
    """

    def __init__(self, failure_code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.failure_code = failure_code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class _TreeMeta:
    hash: str
    file_count: int
    byte_count: int


# ── 진입점 ────────────────────────────────────────────────────────────────


@contextmanager
def fetch(spec: Mapping[str, Any]) -> Iterator[FetchedInput]:
    """method에 따라 fetch하고 스캔 루트를 담은 `FetchedInput`을 내어준다.

    `materialize.materialize()`와 동일하게 `with` 블록을 빠져나가면 임시 디렉터리를
    지운다 -- `/analysis-inputs`도 fetch한 코드 원문을 디스크에 남기지 않는다(D2는
    "두 번째 fetch(`/analysis`)가 캐시를 재사용하지 않는다"는 뜻이지, 이 첫 fetch가
    원칙을 벗어나도 된다는 뜻이 아니다).
    """
    method = spec.get("method")
    with tempfile.TemporaryDirectory(prefix="analysis-input-") as tmp:
        if method == "GITHUB_URL":
            yield _fetch_github(spec, tmp)
            return
        if method == "ZIP_WITH_GITLOG":
            yield _fetch_zip(spec, tmp)
            return
        raise FetchError("INVALID_REPOSITORY_URL", f"알 수 없는 method입니다: {method!r}")


@contextmanager
def refetch_pinned(descriptor: Mapping[str, Any]) -> Iterator[FetchedInput]:
    """D2 — `POST /analysis`가 `/analysis-inputs`의 결과를 캐시 없이 재현한다.

    GITHUB_URL은 브랜치가 아니라 `headCommit.sha`로 정확히 고정해 재클론한다(그 사이
    브랜치가 움직였어도 검증했던 바로 그 코드를 받는다). ZIP은 같은 다운로드 URL로
    재다운로드한다. 재fetch 후 `input_hash`가 descriptor의 값과 같은지 반드시 확인하고,
    다르면 하드 실패시킨다 -- "검증했던 것과 다른 코드"를 그대로 분석하면 안 된다.
    """
    method = descriptor.get("method")
    expected_hash = (descriptor.get("input_hash") or "").strip()
    with tempfile.TemporaryDirectory(prefix="analysis-refetch-") as tmp:
        if method == "GITHUB_URL":
            sha = (descriptor.get("head_commit_sha") or "").strip()
            if not sha:
                raise FetchError("INVALID_REPOSITORY_URL", "재fetch에 headCommit.sha가 필요합니다")
            result = _fetch_github_pinned(descriptor, tmp, sha)
        elif method == "ZIP_WITH_GITLOG":
            result = _fetch_zip(descriptor, tmp)
        else:
            raise FetchError("INVALID_REPOSITORY_URL", f"알 수 없는 method입니다: {method!r}")

        if expected_hash and result.input_hash != expected_hash:
            raise FetchError(
                "INPUT_HASH_MISMATCH",
                "재fetch한 코드가 검증했던 입력과 다릅니다(inputHash 불일치) -- "
                "브랜치가 그 사이 바뀌었을 수 있습니다",
            )
        yield result


def derive_analysis_input_id(*, org_id: str, method: str, source: str, pin: str) -> str:
    """D2 — 서버 상태 없이 requestId 멱등성을 만족시키는 결정론적 id.

    같은 (기관, method, 소스, 고정값) 조합이면 인스턴스·재배포와 무관하게 항상 같은
    UUID가 나온다. `analysis_input_id_mode="random"`이면 매번 새 UUID(팀 레포를 여러
    팀원이 제출할 때 같은 id가 나오는 게 백엔드 컬럼 제약과 안 맞을 경우의 대비책).
    """
    settings = get_settings()
    if settings.analysis_input_id_mode == "random":
        return str(uuid.uuid4())
    name = f"{org_id}|{method}|{source}|{pin}"
    return str(uuid.uuid5(_ANALYSIS_INPUT_NAMESPACE, name))


# ── GITHUB_URL ───────────────────────────────────────────────────────────

_STDERR_PATTERNS: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"remote branch .* not found in upstream", re.I), "BRANCH_NOT_FOUND", False),
    (re.compile(r"repository .* not found|not found", re.I), "REPO_NOT_FOUND", False),
    (re.compile(
        r"authentication failed|could not read username|terminal prompts disabled|"
        r"403|access denied",
        re.I,
    ), "REPOSITORY_ACCESS_DENIED", False),
    (re.compile(
        r"could not resolve host|connection (reset|refused|timed out)|"
        r"temporary failure|429|50[0-9]|early eof",
        re.I,
    ), "TEMPORARY_ERROR", True),
]

# https://user:token@host 형태의 자격증명과 흔한 GitHub 토큰 접두사를 stderr에서 지운다.
# githubInstallationId(설치 토큰)가 도입되면 이 stderr가 job.failure_reason으로
# Spring DB에 그대로 남으므로 지금부터 마스킹해 둔다.
_CRED_IN_URL_RE = re.compile(r"https://[^@\s]*@")
_TOKEN_RE = re.compile(r"gh[ps]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")


def _redact(text: str) -> str:
    text = _CRED_IN_URL_RE.sub("https://", text)
    text = _TOKEN_RE.sub("***", text)
    return text


def _allowed_repo_hosts() -> set[str]:
    raw = get_settings().allowed_repo_hosts
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _validate_host(repo_url: str) -> None:
    try:
        materialize.validate_repo_url(repo_url)
    except ValueError as exc:
        raise FetchError("INVALID_REPOSITORY_URL", str(exc)) from exc
    host = urlparse(repo_url).netloc.lower()
    allowed = _allowed_repo_hosts()
    if allowed and host not in allowed:
        raise FetchError("UNSUPPORTED_HOST", f"지원하지 않는 호스트입니다: {host}")


def _classify_github_error(stderr: bytes) -> FetchError:
    text = _redact(stderr.decode(errors="replace"))
    lines = text.strip().splitlines()
    tail = lines[-1] if lines else "git clone 실패"
    for pattern, code, retryable in _STDERR_PATTERNS:
        if pattern.search(text):
            return FetchError(code, f"레포를 가져오지 못했습니다: {tail}", retryable=retryable)
    # 어느 패턴에도 안 걸리면 TEMPORARY_ERROR로 -- 재시도해서 나쁠 것 없고, 실패
    # 사유를 모른 채 영구 실패(false)로 단정하는 것보다 안전하다.
    return FetchError("TEMPORARY_ERROR", f"레포를 가져오지 못했습니다: {tail}", retryable=True)


def _current_branch(repo_dir: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, timeout=10, env=materialize.git_env(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    branch = out.stdout.decode(errors="replace").strip()
    return branch or None


def _head_commit(repo_dir: str) -> dict[str, Any] | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "log", "-1", f"--format=%H{_FS}%cI{_FS}%B"],
            check=True, capture_output=True, timeout=10, env=materialize.git_env(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    # maxsplit=2 -- message(%B)는 개행·임의 바이트를 포함할 수 있으니 나머지 전부를 그대로 문다.
    parts = out.stdout.decode(errors="replace").strip("\n").split(_FS, 2)
    if len(parts) != 3:
        return None
    sha, committed_at, message = parts
    return {"sha": sha, "message": message.strip(), "committed_at": committed_at}


def _iso_days_ago(days: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _parse_git_log(repo_dir: str, max_commits: int) -> list[dict[str, Any]]:
    """`git log --numstat`을 GitCommit 딕셔너리 목록으로. 커밋 메시지는 안 담는다

    (gitHistory[] 항목엔 메시지가 없다 -- headCommit에만 있다). 필드가 고정 길이
    (sha/작성자명/이메일/시각)라 값 하나에 구분자가 우연히 섞여도 다른 커밋 파싱까지
    깨지진 않는다.

    🔴 merge 커밋은 `-m --first-parent`로 diff를 강제로 만든다 -- 안 하면
    `--numstat`이 merge 커밋에 대해 아무 줄도 안 내서 changedFiles/additions/deletions가
    조용히 0/빈 배열이 된다(이 사실을 모르고 소비하면 "이 커밋은 변경이 없었다"로
    오독하게 된다).

    🔴 `--no-textconv` 필수 -- 실측 확인(2026-08-06): `.gitattributes`가 `diff=X`를
    선언하고 `.git/config`에 `[diff "X"] textconv = <임의명령>`이 있으면, **`-p` 없이
    `--numstat`만 줘도** 그 명령이 실행된다(`core.fsmonitor`는 log/rev-parse에서 전혀
    발동 안 하는데 이건 발동함 -- 직접 재현해서 확인). GITHUB_URL 경로는 매번 새로
    클론해 `.git/config`를 우리가 직접 만드므로 이 벡터가 통할 여지가 원래도 적지만,
    비용 없는 방어라 여기서도 건다.
    """
    fmt = f"{_RS}%H{_FS}%an{_FS}%ae{_FS}%cI"
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "log", f"--max-count={max_commits}",
             "-m", "--first-parent", "--numstat", "--no-textconv", f"--format={fmt}"],
            check=True, capture_output=True, timeout=10, env=materialize.git_env(),
        )
    except (subprocess.SubprocessError, OSError):
        return []
    return _parse_git_log_output(out.stdout.decode(errors="replace"))


def _parse_git_log_output(raw: str) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    for block in raw.split(_RS):
        block = block.strip("\n")
        if not block:
            continue
        header, _, rest = block.partition("\n")
        parts = header.split(_FS)
        if len(parts) != 4:
            continue
        sha, author_name, author_email, committed_at = parts
        changed_files: list[str] = []
        additions = deletions = 0
        for line in rest.splitlines():
            fields = line.strip().split("\t")
            if len(fields) != 3:
                continue
            add, dele, path = fields
            changed_files.append(path)
            if add.isdigit():
                additions += int(add)
            if dele.isdigit():
                deletions += int(dele)
        commits.append({
            "sha": sha, "author_name": author_name, "author_email": author_email,
            "committed_at": committed_at, "changed_files": changed_files,
            "additions": additions, "deletions": deletions,
        })
    return commits


def _try_deepen_history(repo_dir: str) -> tuple[list[dict[str, Any]], bool, str]:
    """D1 Phase B — best-effort, 별도의 짧은 시간 예산. 실패해도 절대 예외를 안 던진다."""
    settings = get_settings()
    since = _iso_days_ago(settings.git_history_since_days)
    try:
        subprocess.run(
            ["git", "-C", repo_dir, "fetch", "--shallow-since", since, "origin"],
            check=True, capture_output=True, timeout=settings.git_history_budget_s,
            env=materialize.git_env(),
        )
    except (subprocess.SubprocessError, OSError):
        # depth-1 그대로 남는다 -- Phase A 결과(코드 자체)는 이 실패와 무관하게 이미 확보됨.
        # `--shallow-since`가 윈도우 밖(레포가 그보다 오래됨)이면 git 자체가
        # "error processing shallow info" 로 실패한다(실측 확인, 2026-08-06 octocat/
        # Hello-World로 재현). head_commit에 이미 있는 정보를 git_history에 없는 필드로
        # 채워 중복시키지 않는다 -- 정직하게 NONE으로 내려간다.
        return [], False, "NONE"

    history = _parse_git_log(repo_dir, settings.git_history_max_commits)
    truncated = len(history) >= settings.git_history_max_commits
    return history, truncated, "REMOTE_DEEPEN"


def _fetch_github(spec: Mapping[str, Any], tmp: str) -> FetchedInput:
    repo_url = (spec.get("repository_url") or "").strip()
    if not repo_url:
        raise FetchError("INVALID_REPOSITORY_URL", "repositoryUrl이 없습니다")
    _validate_host(repo_url)

    branch = (spec.get("requested_branch") or "").strip()
    if branch:
        try:
            materialize.validate_branch(branch)
        except ValueError as exc:
            raise FetchError("BRANCH_NOT_FOUND", str(exc)) from exc

    settings = get_settings()
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += ["--", repo_url, tmp]

    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       timeout=settings.analysis_input_clone_timeout_s, env=materialize.git_env())
    except subprocess.CalledProcessError as exc:
        raise _classify_github_error(exc.stderr or b"") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(
            "TEMPORARY_ERROR",
            f"클론이 {settings.analysis_input_clone_timeout_s}초를 넘겼습니다",
            retryable=True,
        ) from exc

    resolved_branch = _current_branch(tmp) or branch or None
    head_commit = _head_commit(tmp)
    git_history, truncated, source = _try_deepen_history(tmp)
    meta = _hash_tree(tmp)

    return FetchedInput(
        root=tmp, method="GITHUB_URL", resolved_branch=resolved_branch,
        head_commit=head_commit, git_history=git_history, git_history_source=source,
        history_truncated=truncated, input_hash=meta.hash,
        file_count=meta.file_count, byte_count=meta.byte_count,
    )


_SHA_RE = re.compile(r"[0-9a-f]{40}$|[0-9a-f]{64}$")


def _fetch_github_pinned(descriptor: Mapping[str, Any], tmp: str, sha: str) -> FetchedInput:
    """D2 재fetch — 브랜치가 아니라 정확한 커밋에 고정한다.

    `git clone --branch <sha>`는 안 된다(--branch는 ref만 받고 임의 sha는 거부한다).
    대신 remote를 만들고 그 sha 하나만 fetch한다. 호스트가 SHA-in-want를 거부하면
    브랜치 클론 후 HEAD가 기대한 sha와 같은지 검증하는 것으로 대체한다 -- 거기서
    불일치가 나면 그게 바로 D2가 잡으려는 "그 사이 브랜치가 움직였다" 상황이라
    실패가 맞는 동작이다.
    """
    if not _SHA_RE.fullmatch(sha):
        raise FetchError("INVALID_REPOSITORY_URL", f"headCommit.sha 형식이 아닙니다: {sha!r}")

    repo_url = (descriptor.get("repository_url") or "").strip()
    if not repo_url:
        raise FetchError("INVALID_REPOSITORY_URL", "repositoryUrl이 없습니다")
    _validate_host(repo_url)

    settings = get_settings()
    env = materialize.git_env()
    timeout = settings.analysis_input_clone_timeout_s

    try:
        subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True,
                       timeout=10, env=env)
        subprocess.run(["git", "-C", tmp, "remote", "add", "origin", "--", repo_url],
                       check=True, capture_output=True, timeout=10, env=env)
        subprocess.run(["git", "-C", tmp, "fetch", "--depth", "1", "origin", "--", sha],
                       check=True, capture_output=True, timeout=timeout, env=env)
        subprocess.run(["git", "-C", tmp, "checkout", "-q", "FETCH_HEAD"],
                       check=True, capture_output=True, timeout=10, env=env)
    except subprocess.TimeoutExpired as exc:
        raise FetchError("TEMPORARY_ERROR", f"재fetch가 {timeout}초를 넘겼습니다", retryable=True) from exc
    except subprocess.CalledProcessError as exc:
        # 호스트가 SHA-in-want를 거부했을 수 있다 -- 브랜치를 클론해서 sha 일치를 검증하는
        # 폴백으로 넘어간다.
        branch = (descriptor.get("resolved_branch") or "").strip()
        return _fetch_github_pinned_via_branch_verify(repo_url, branch, sha, tmp)

    head_commit = _head_commit(tmp)
    git_history, truncated, source = _try_deepen_history(tmp)
    meta = _hash_tree(tmp)
    return FetchedInput(
        root=tmp, method="GITHUB_URL", resolved_branch=descriptor.get("resolved_branch"),
        head_commit=head_commit, git_history=git_history, git_history_source=source,
        history_truncated=truncated, input_hash=meta.hash,
        file_count=meta.file_count, byte_count=meta.byte_count,
    )


def _fetch_github_pinned_via_branch_verify(
    repo_url: str, branch: str, expected_sha: str, tmp: str
) -> FetchedInput:
    settings = get_settings()
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += ["--", repo_url, tmp]
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       timeout=settings.analysis_input_clone_timeout_s, env=materialize.git_env())
    except subprocess.CalledProcessError as exc:
        raise _classify_github_error(exc.stderr or b"") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError("TEMPORARY_ERROR", "재fetch(브랜치 폴백)가 타임아웃됐습니다",
                         retryable=True) from exc

    actual_sha = materialize.head_sha(tmp)
    if actual_sha != expected_sha:
        raise FetchError(
            "FETCH_FAILED",
            f"브랜치 HEAD가 검증 시점과 다릅니다(기대 {expected_sha}, 실제 {actual_sha}) -- "
            "그 사이 새 커밋이 푸시된 것으로 보입니다",
        )
    head_commit = _head_commit(tmp)
    git_history, truncated, source = _try_deepen_history(tmp)
    meta = _hash_tree(tmp)
    return FetchedInput(
        root=tmp, method="GITHUB_URL", resolved_branch=branch or None,
        head_commit=head_commit, git_history=git_history, git_history_source=source,
        history_truncated=truncated, input_hash=meta.hash,
        file_count=meta.file_count, byte_count=meta.byte_count,
    )


# ── ZIP_WITH_GITLOG ───────────────────────────────────────────────────────


def _download(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("ARCHIVE_INVALID", f"downloadUrl은 http(s)만 허용합니다: {url!r}")

    settings = get_settings()
    allowed = {h.strip().lower() for h in settings.allowed_storage_hosts.split(",") if h.strip()}
    if not allowed:
        raise FetchError(
            "ARCHIVE_INVALID",
            "ALLOWED_STORAGE_HOSTS가 설정되지 않아 다운로드를 거부합니다(fail-closed 기본값)",
        )
    if parsed.netloc.lower() not in allowed:
        raise FetchError("ARCHIVE_INVALID", f"허용되지 않은 다운로드 호스트입니다: {parsed.netloc}")

    try:
        # follow_redirects=False -- 리다이렉트를 허용하면 허용목록 검사를 우회해
        # 다른 호스트로 갈 수 있다(SSRF). 원본 URL의 호스트만 신뢰한다.
        resp = httpx.get(url, timeout=settings.analysis_input_clone_timeout_s, follow_redirects=False)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchError("TEMPORARY_ERROR", f"ZIP 다운로드 실패: {exc}", retryable=True) from exc

    if len(resp.content) > rules.MAX_TOTAL_BYTES:
        raise FetchError("FILE_TOO_LARGE", f"ZIP 크기가 한도를 넘습니다: {len(resp.content)} bytes")
    return resp.content


def _fetch_zip(spec: Mapping[str, Any], tmp: str) -> FetchedInput:
    download_url = (spec.get("download_url") or "").strip()
    storage_uri = (spec.get("storage_uri") or "").strip()

    if not download_url and storage_uri.startswith("s3://"):
        # 지금 이 서비스엔 boto3/AWS 자격증명이 전혀 없다(requirements.txt 확인) --
        # 조용히 500 내는 대신 명확한 사유로 즉시 실패시킨다.
        raise FetchError(
            "ARCHIVE_INVALID",
            "s3:// 스토리지 URI는 아직 지원하지 않습니다 -- presigned HTTPS URL(downloadUrl)로 "
            "보내주세요",
        )
    download_url = download_url or storage_uri
    if not download_url:
        raise FetchError("ARCHIVE_INVALID", "storageUri/downloadUrl이 없습니다")

    zip_bytes = _download(download_url)

    tmp_path = Path(tmp)
    try:
        rules._safe_extract(zip_bytes, tmp_path)
    except zipfile.BadZipFile as exc:
        raise FetchError("ARCHIVE_INVALID", f"ZIP 파일이 손상되었거나 해제할 수 없습니다: {exc}") from exc
    except ValueError as exc:
        raise _classify_zip_error(exc) from exc

    root = str(rules._repo_root(tmp_path))

    backend_history = spec.get("git_history")
    if backend_history:
        git_history: list[dict[str, Any]] = list(backend_history)
        source = "BACKEND_SUPPLIED"
    else:
        git_history, source = _try_embedded_git_history(root)

    settings = get_settings()
    if not git_history and source == "NONE" and settings.zip_require_git_log:
        raise FetchError("GIT_LOG_MISSING", "git 이력을 확인할 수 없습니다")

    meta = _hash_tree(root)
    if meta.file_count == 0:
        raise FetchError("EMPTY_CODE", "ZIP 안에 분석할 코드가 없습니다")

    return FetchedInput(
        root=root, method="ZIP_WITH_GITLOG", resolved_branch=None, head_commit=None,
        git_history=git_history, git_history_source=source, history_truncated=False,
        input_hash=meta.hash, file_count=meta.file_count, byte_count=meta.byte_count,
    )


def _classify_zip_error(exc: ValueError) -> FetchError:
    msg = str(exc)
    if "너무 많습니다" in msg or "한도를 넘습니다" in msg:
        return FetchError("FILE_TOO_LARGE", msg)
    if "벗어납니다" in msg:
        return FetchError("PROHIBITED_FILE", msg)
    return FetchError("ARCHIVE_INVALID", msg)


# D-git-rce (2026-08-06): ZIP 안의 `.git`을 그대로 파싱하면 안 된다.
#   WHY: 학생이 ZIP에 `.git/config`를 조작해 넣을 수 있다 -- `core.fsmonitor`는
#   git이 특정 명령을 실행할 때마다 부르는 임의 셸 명령 훅이고, `core.hooksPath`·
#   `include.path`도 같은 클래스의 위험이다. "코드는 읽기만 한다"(materialize.py
#   D12 주석)는 원칙이 임베디드 `.git`을 그대로 신뢰하는 순간 깨진다.
#   COST: 임베디드 저장소의 진짜 커스텀 설정(예: 커스텀 diff 드라이버)은 못 쓴다 --
#   여기선 log/rev-parse만 하니 사실상 비용 없음.
#   EXIT: 이 정도로도 못 미더우면 임베디드 `.git` 자체를 신뢰 안 하고 항상 NONE으로
#   내려가는 쪽으로 되돌린다(정책 스위치 하나 추가).
_SANDBOX_GIT_ARGS = [
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.ext.allow=never",
    "--no-pager",
]


def _sandbox_git_env() -> dict[str, str]:
    """임베디드 `.git` 파싱 전용 환경 -- 시스템/전역 git 설정을 무력화한다."""
    return {
        **materialize.git_env(),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def _strip_git_config(git_dir: Path) -> None:
    """git을 부르기 전에 위험한 설정 파일을 전부 지운다. `log`/`rev-parse`는 config 없이도 잘 된다."""
    config = git_dir / "config"
    if config.exists():
        config.unlink()
    hooks = git_dir / "hooks"
    if hooks.is_dir():
        shutil.rmtree(hooks, ignore_errors=True)
    # 중첩 .git(서브모듈 등)도 같은 처리 -- 얕게 도는 rglob이라 트리가 커도 안전하다.
    for nested_config in git_dir.rglob(".git/config"):
        nested_config.unlink(missing_ok=True)
    for nested_hooks in git_dir.rglob(".git/hooks"):
        if nested_hooks.is_dir():
            shutil.rmtree(nested_hooks, ignore_errors=True)


def _try_embedded_git_history(root: str) -> tuple[list[dict[str, Any]], str]:
    """D3 경로 ② — ZIP 안에 `.git`이 있으면 직접 파싱한다.

    `log`/`rev-parse`만 쓰고 `fetch`/`remote` 등 네트워크가 필요한 명령은 절대
    안 부른다 -- 임베디드 `.git`은 읽기 전용 데이터 추출 용도로만 신뢰한다.
    """
    root_path = Path(root)
    git_dir = root_path / ".git"
    if not git_dir.is_dir():
        return [], "NONE"

    _strip_git_config(git_dir)
    env = _sandbox_git_env()
    settings = get_settings()

    fmt = f"{_RS}%H{_FS}%an{_FS}%ae{_FS}%cI"
    try:
        out = subprocess.run(
            ["git", *_SANDBOX_GIT_ARGS, "-C", root, "log",
             f"--max-count={settings.git_history_max_commits}",
             "-m", "--first-parent", "--numstat", "--no-textconv", f"--format={fmt}"],
            check=True, capture_output=True, timeout=10, env=env,
        )
    except (subprocess.SubprocessError, OSError):
        return [], "NONE"

    history = _parse_git_log_output(out.stdout.decode(errors="replace"))
    if not history:
        return [], "NONE"
    return history, "EMBEDDED_GIT"


# ── 공통 ─────────────────────────────────────────────────────────────────


def _hash_tree(root: str) -> _TreeMeta:
    """D2 — `input_hash` 재정의. `engine.py`의 기존 스냅샷 해시(ZIP=zip_bytes 자체,

    GITHUB_URL=스캐너-필터링된 파일만)와 다른 별도 정의다 -- 그 해시는 vendor 스캐너가
    바뀔 때마다(흔함, `extractor_version()`이 존재하는 이유) 코드 변경 없이도 값이
    바뀌어서 D2의 무결성 검증(재fetch 후 hash 일치 확인)을 오발동시킨다.

    여기서는 스캔 루트 아래 `.git/**`를 제외한 전 파일을, 상대경로 정렬 순서로
    `경로바이트 + NUL + 원본바이트`를 이어붙여 SHA-256을 낸다. 같은 트리면 ZIP으로
    받든 클론으로 받든 동일한 해시가 나온다(백엔드의 "같은 inputHash면 analysisInputId
    재사용" 요청도 이걸로 공짜로 충족된다).
    """
    root_path = Path(root)
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    paths = sorted(
        p for p in root_path.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root_path).parts
    )
    for path in paths:
        rel = path.relative_to(root_path).as_posix()
        raw = path.read_bytes()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
        file_count += 1
        byte_count += len(raw)
    return _TreeMeta(hash=digest.hexdigest(), file_count=file_count, byte_count=byte_count)
