""" `POST /api/v0/analyses`의 코드 fetch — `materialize.py`의 형제 모듈.

저장소를 검증하고 가져와 스캔 루트를 만든다. `engine.analyze()`가 이걸 쓴다.

  D1  git 히스토리 수집은 커밋 개수가 아니라 **벽시계 시간**으로 상한. 코드 자체를 가져오는
      것(Phase A, 필수)과 히스토리로 풍부화하는 것(Phase B, best-effort)을 분리한다 —
      Phase B가 느리거나 실패해도 Phase A 결과는 절대 버리지 않는다.
  D3  ZIP의 git 히스토리는 ①백엔드가 요청에 실어 보내면 그것 우선 ②ZIP 안에 `.git`이 있으면
      직접 파싱 ③둘 다 없으면 실패시키지 않고 빈 값으로 진행(`ZIP_REQUIRE_GIT_LOG`로 정책 전환 가능).

🔴 **옛 `/analysis-inputs` 분리 API는 폐기됐다**(2026-08-06 팀원 확인, 2026-08-07 재삭제).
`POST /analyses`를 검증·fetch·분석 3개로 쪼개자는 건 백엔드 쪽 착오였고 근거 문서
`api-request-to-ai-server.md`도 함께 무효다. **`/analyses`가 한 몸으로 처리한다.**
그때 딸려 있던 D2(재fetch로 무결성 재확인)와 `refetch_pinned()`·`INPUT_HASH_MISMATCH`도
같이 사라졌다 — 한 번에 fetch하므로 재현할 대상이 없다. 이 모듈에 남은 것은 검증·fetch·
히스토리 수집·해시 계산이고, 그건 분리와 무관하게 필요한 일이다.

`materialize.py`의 D12 방어(`_validate_scheme`/`validate_branch`/`git_env`)를 그대로 재사용한다
— 복사하면 한쪽만 패치되고 다른 쪽이 낡는다(vendor drift로 실제 겪은 사고와 같은 클래스).
호스트 허용목록만은 예외 -- materialize.py는 클론 경로용으로 github.com 하나에 고정하지만
이 모듈은 자체 설정값(`_allowed_repo_hosts`)을 따로 쓴다(`_validate_host` 참고).
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

import httpx

from app.config import get_settings

# D2(2026-08-25): 이 모듈은 원래 로그가 전혀 없었다 -- 2026-08-25 인시던트에서
#   job이 "분석 시작"(jobs.py) 이후 fetch 단계 어딘가에서 클론 타임아웃(300초)의
#   3배 넘게(16분+) 멈췄는데, 어느 줄에서 멈췄는지 로그로 전혀 구분이 안 됐다.
#   WHY: subprocess.run(timeout=...)이 코드상으로는 견고한데도 왜 안 풀렸는지
#        재현 전까지는 추측만 가능하다 -- 다음 발생 시 최소한 "어느 단계"인지는
#        로그로 바로 좁혀야 한다.
#   COST: 학생 코드 자체(경로·브랜치명 정도)가 로그에 남는다 -- 프롬프트 본문·
#        코드 내용은 원래도 stages.py 원칙대로 안 남긴다.
#   EXIT: 원인이 확정되면(예: subprocess 자체의 알려진 문제) 이 로그들은 정상
#        경로에서 소음만 되므로 DEBUG로 낮추거나 지운다.
log = logging.getLogger(__name__)
from app.engines.analysis import materialize, rules

# git log 파싱용 구분자. ASCII 제어문자라 커밋 메시지·작성자명에 우연히 섞일 가능성이
# 극히 낮다 -- 완벽한 injection-proof 파서는 아니지만(학생이 의도적으로 이 바이트를
# 커밋 메시지에 넣으면 그 한 커밋의 필드가 깨질 수 있다), 여기는 텍스트 추출일 뿐이라
# 최악의 경우도 "그 커밋 메타데이터가 잘못 파싱됨"이지 실행/보안 문제로 안 이어진다.
# (진짜 보안 경계는 임베디드 .git 실행 방어 쪽 -- 아래 D-git-rce 참조.)
_RS = "\x1e"
_FS = "\x1f"

# _parse_git_log/_try_embedded_git_history 공용 git log 포맷. %P(부모 SHA, 공백구분)로
# isMergeCommit/parentSha를, %aI(author date)로 authoredAt을, %s(subject 1줄)로
# isRevertCommit 판정과 commitMessage를 뽑는다.
# ⚠️ 옛 원칙("메시지는 headCommit에만 둔다")은 폐기됐다 -- 프론트 "최근 커밋 이력"
# 화면이 커밋마다 메시지를 쓴다는 게 2026-08-07에 확인됐다. 그전엔 파싱만 하고 버렸다.
_LOG_FORMAT = f"{_RS}%H{_FS}%P{_FS}%an{_FS}%ae{_FS}%aI{_FS}%cI{_FS}%s"

# git revert / GitHub "Revert PR" 버튼이 자동 생성하는 커밋 제목 포맷(정확히 이 접두어).
# 정밀도 우선 -- 오탐이 기여도를 부당하게 깎는 게 미탐보다 나쁘다(D-analysis-b1).
_REVERT_SUBJECT_RE = re.compile(r'^Revert "')

# GitHub App형 봇 계정의 noreply 이메일 표기(예: 41898282+github-actions[bot]@users.noreply.github.com).
# 대괄호 없는 커스텀 서비스 계정(예: 그냥 이름이 "CI")은 의도적으로 범위 밖 -- 유지보수
# 필요한 이름 목록 없이 GitHub 표준 표기만으로 정밀도를 지킨다.
_BOT_EMAIL_RE = re.compile(r"^\d+\+[\w-]+\[bot\]@users\.noreply\.github\.com$")


@dataclass(frozen=True)
class FetchedInput:
    """fetch() 한 번의 결과. `engine.analyze()`가 스캔 루트와 git 메타를 여기서 가져간다."""

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


# fetch()가 내는 11개 failureCode. 이 모듈이
# 내는 값은 항상 이 집합 안에 있어야 한다 -- 백엔드 DB CHECK 제약이 이 문자열 그대로다.
# 11종 전부 `analysis_job.failure_code`의 15종 안에 같은 이름으로 들어 있다(2026-08-07 회신).
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



class FetchError(Exception):
    """failureCode 하나로 분류된 fetch 실패. `jobs.py`가 job의 `failure_code`로 옮긴다."""

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
def fetch(spec: Mapping[str, Any], zip_bytes: bytes | None = None) -> Iterator[FetchedInput]:
    """method에 따라 fetch하고 스캔 루트를 담은 `FetchedInput`을 내어준다.

    `materialize.materialize()`와 동일하게 `with` 블록을 빠져나가면 임시 디렉터리를
    지운다 -- fetch한 코드 원문을 디스크에 남기지 않는다.

    `zip_bytes`(M4, engine.py의 기존 경로 통합용) -- `/analyses`의 멀티파트 업로드는
    downloadUrl 없이 바이트를 직접 들고 있다. 있으면 `_download()`를 건너뛴다.
    """
    method = spec.get("method")
    log.info("fetch 시작 method=%s", method)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="analysis-input-") as tmp:
        log.info("fetch: 임시디렉터리 생성 완료 %.1fs", time.monotonic() - t0)
        if method == "GITHUB_URL":
            yield _fetch_github(spec, tmp)
            log.info("fetch 종료 method=GITHUB_URL 총 %.1fs", time.monotonic() - t0)
            return
        if method == "ZIP_WITH_GITLOG":
            yield _fetch_zip(spec, tmp, zip_bytes)
            log.info("fetch 종료 method=ZIP_WITH_GITLOG 총 %.1fs", time.monotonic() - t0)
            return
        raise FetchError("INVALID_REPOSITORY_URL", f"알 수 없는 method입니다: {method!r}")


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
    # scheme만 materialize의 D12 방어를 재사용한다(ext::/file:: 등 서브프로토콜 차단) --
    # 호스트 허용목록은 이 모듈 자체의 설정 가능한 값(_allowed_repo_hosts)을 쓴다.
    # materialize.validate_repo_url()을 통째로 쓰면 그쪽의 고정 단일 호스트(github.com만)
    # 정책이 여기 UNSUPPORTED_HOST 분류를 덮어써버린다(2026-08-07 develop 병합 시 확인).
    try:
        materialize._validate_scheme(repo_url)
    except ValueError as exc:
        raise FetchError("INVALID_REPOSITORY_URL", str(exc)) from exc
    host = urlparse(repo_url).netloc.lower()
    allowed = _allowed_repo_hosts()
    # 🔴 fail-closed. 옛 `if allowed and host not in allowed`는 ALLOWED_REPO_HOSTS가
    # 비어 있으면(오타·주석처리·빈 값) 검사를 통째로 건너뛰어 **아무 호스트나 clone**
    # 됐다. 같은 파일 _download()는 이미 fail-closed인데 더 위험한 clone 쪽이 더
    # 느슨했다 -- 설정 실수 하나가 방어를 지우면 안 된다.
    if not allowed:
        raise FetchError(
            "UNSUPPORTED_HOST",
            "ALLOWED_REPO_HOSTS가 설정되지 않아 클론을 거부합니다(fail-closed 기본값)",
        )
    if host not in allowed:
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
    return {"commit_hash": sha, "commit_message": message.strip(),
            "committed_at": committed_at}


def _iso_days_ago(days: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _parse_git_log(repo_dir: str, max_commits: int) -> list[dict[str, Any]]:
    """`git log --numstat`을 GitCommit 딕셔너리 목록으로.

    branch_name은 여기서 안 채운다 -- 호출자가
    resolved_branch를 이미 알고 있어 FetchedInput 생성 직전에 후처리로 주입한다(ZIP
    경로는 브랜치 개념 자체가 없어 이 저수준 함수 하나가 두 의미를 못 담기 때문).

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
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "log", f"--max-count={max_commits}",
             "-m", "--first-parent", "--numstat", "--no-textconv", f"--format={_LOG_FORMAT}"],
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
        # maxsplit=6 -- subject(%s)는 git이 개행을 안 담는다고 보장하지만, 구분자 바이트가
        # 우연히 섞여도(작성자명 등과 같은 기존 위험) 나머지 전부를 subject 쪽으로 몰아
        # 앞쪽 고정 필드(sha/parents/name/email/dates) 파싱이 안 깨지게 한다.
        parts = header.split(_FS, 6)
        if len(parts) != 7:
            continue
        sha, parents_raw, author_name, author_email, authored_at, committed_at, subject = parts
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
        parent_shas = parents_raw.split()
        commits.append({
            "commit_hash": sha, "commit_message": subject,
            "author_name": author_name, "author_email": author_email,
            "committed_at": committed_at, "changed_files": changed_files,
            "additions": additions, "deletions": deletions,
            "authored_at": authored_at,
            # root 커밋(부모 없음)은 **null**이다. 백엔드가 2026-08-07에
            # commit_attribution.parent_commit_hash의 NOT NULL을 해제해서, 옛 all-zero
            # sentinel("0"*40, git pre-receive hook 관행)을 쓸 이유가 사라졌다 --
            # sentinel은 "부모가 all-zero 해시"라는 거짓 사실을 원장에 남긴다.
            "parent_sha": parent_shas[0] if parent_shas else None,
            "is_merge_commit": len(parent_shas) >= 2,
            "is_revert_commit": bool(_REVERT_SUBJECT_RE.match(subject)),
            "is_bot_commit": (
                bool(_BOT_EMAIL_RE.match(author_email)) or author_name.endswith("[bot]")
            ),
            "changed_line_count": additions + deletions,
        })
    return commits


def _tag_branch_name(history: list[dict[str, Any]], branch_name: str | None) -> list[dict[str, Any]]:
    """`_parse_git_log_output`은 branch_name을 안 채운다 -- resolved_branch를 이미 아는

    호출자가 `FetchedInput` 생성 직전에 여기서 일괄 주입한다. git엔 "이 커밋이 어느
    브랜치 소속인가"라는 개념이 없다 -- 이 값은 어디까지나 "이 fetch가 resolve한
    브랜치를 반환된 히스토리 전체에 균일 적용"한 것이지 커밋별 진짜 소속이 아니다.
    미상이면 NOT NULL을 만족시키는 빈 문자열.
    """
    return [{**c, "branch_name": branch_name or ""} for c in history]


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
    t0 = time.monotonic()
    repo_url = (spec.get("repository_url") or "").strip()
    if not repo_url:
        raise FetchError("INVALID_REPOSITORY_URL", "repositoryUrl이 없습니다")
    _validate_host(repo_url)
    log.info("fetch_github: 호스트 검증 통과 %.1fs", time.monotonic() - t0)

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

    log.info("fetch_github: git clone 시작 timeout=%ss %.1fs",
             settings.analysis_input_clone_timeout_s, time.monotonic() - t0)
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       timeout=settings.analysis_input_clone_timeout_s, env=materialize.git_env())
    except subprocess.CalledProcessError as exc:
        log.warning("fetch_github: git clone 실패(CalledProcessError) %.1fs", time.monotonic() - t0)
        raise _classify_github_error(exc.stderr or b"") from exc
    except subprocess.TimeoutExpired as exc:
        log.warning("fetch_github: git clone 타임아웃 %.1fs", time.monotonic() - t0)
        raise FetchError(
            "TEMPORARY_ERROR",
            f"클론이 {settings.analysis_input_clone_timeout_s}초를 넘겼습니다",
            retryable=True,
        ) from exc
    except OSError as exc:
        # 🔴 2026-08-10 배포본 장애: App Runner 관리형 런타임에 git 바이너리가 없어
        # subprocess가 FileNotFoundError를 던졌는데, 여기서 안 잡혀 jobs.py의 catch-all로
        # 새어나가 `MODEL_ERROR`로 보고됐다. LLM은 한 번도 안 불렀는데(aiUsage: []) 백엔드는
        # "모델 실패"로 읽는다. 같은 파일 _try_embedded_git_history는 이미 OSError까지
        # 잡고 있었다 -- 이쪽만 안 맞춰져 있던 것이다.
        #   TEMPORARY_ERROR: GITHUB_FAILURE_CODES 6종 중 "환경이 깨졌다"에 가장 가까운 값.
        #   retryable=False: 바이너리 부재는 재시도로 안 풀린다(타임아웃과 다른 점).
        raise FetchError(
            "TEMPORARY_ERROR", f"저장소를 가져오지 못했습니다: {exc}",
        ) from exc

    log.info("fetch_github: git clone 완료 %.1fs", time.monotonic() - t0)
    resolved_branch = _current_branch(tmp) or branch or None
    head_commit = _head_commit(tmp)
    log.info("fetch_github: head_commit 조회 완료 %.1fs", time.monotonic() - t0)
    git_history, truncated, source = _try_deepen_history(tmp)
    log.info("fetch_github: git_history 수집 완료 %.1fs", time.monotonic() - t0)
    git_history = _tag_branch_name(git_history, resolved_branch)
    meta = _hash_tree(tmp)
    log.info("fetch_github: hash_tree 완료 %.1fs file_count=%d", time.monotonic() - t0, meta.file_count)
    # ZIP 경로(_fetch_zip)와 같은 검사. 없으면 빈 레포·코드 없는 레포가 검증을 통과하고,
    # 실패가 한참 뒤 분석 단계에서 다른 사유로 나온다 -- 백엔드가 EMPTY_CODE를 15종에
    # 넣어준 게 정확히 이 사유를 구분하려던 것이다.
    if meta.file_count == 0:
        raise FetchError("EMPTY_CODE", "레포에 분석할 코드가 없습니다")

    return FetchedInput(
        root=tmp, method="GITHUB_URL", resolved_branch=resolved_branch,
        head_commit=head_commit, git_history=git_history, git_history_source=source,
        history_truncated=truncated, input_hash=meta.hash,
        file_count=meta.file_count, byte_count=meta.byte_count,
    )


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

    # 🔴 스트리밍으로 받으며 상한을 넘는 순간 끊는다. 옛 httpx.get()은 본문 전체를
    # 메모리에 받은 **뒤에** len()을 재서, 상한 검사에 도달하기 전에 프로세스가 죽었다
    # (App Runner 단일 인스턴스라 그 순간 다른 모든 요청도 같이 죽는다). 허용목록이
    # 있으니 임의 호스트는 아니지만, 허용목록 하나가 방어의 전부여선 안 된다.
    chunks: list[bytes] = []
    received = 0
    try:
        # follow_redirects=False -- 리다이렉트를 허용하면 허용목록 검사를 우회해
        # 다른 호스트로 갈 수 있다(SSRF). 원본 URL의 호스트만 신뢰한다.
        with httpx.stream(
            "GET", url, timeout=settings.analysis_input_clone_timeout_s,
            follow_redirects=False,
        ) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                received += len(chunk)
                if received > rules.MAX_TOTAL_BYTES:
                    raise FetchError(
                        "FILE_TOO_LARGE",
                        f"ZIP 크기가 한도({rules.MAX_TOTAL_BYTES} bytes)를 넘습니다",
                    )
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise FetchError("TEMPORARY_ERROR", f"ZIP 다운로드 실패: {exc}", retryable=True) from exc

    return b"".join(chunks)


def _fetch_zip(spec: Mapping[str, Any], tmp: str, zip_bytes: bytes | None = None) -> FetchedInput:
    if zip_bytes is None:
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

    try:
        out = subprocess.run(
            ["git", *_SANDBOX_GIT_ARGS, "-C", root, "log",
             f"--max-count={settings.git_history_max_commits}",
             "-m", "--first-parent", "--numstat", "--no-textconv", f"--format={_LOG_FORMAT}"],
            check=True, capture_output=True, timeout=10, env=env,
        )
    except (subprocess.SubprocessError, OSError):
        return [], "NONE"

    history = _parse_git_log_output(out.stdout.decode(errors="replace"))
    if not history:
        return [], "NONE"
    # ZIP 경로는 브랜치 개념 자체가 없다(resolved_branch가 늘 None) -- NOT NULL을
    # 만족시키는 빈 문자열 sentinel.
    history = [{**c, "branch_name": ""} for c in history]
    return history, "EMBEDDED_GIT"


# ── 공통 ─────────────────────────────────────────────────────────────────


def _hash_tree(root: str) -> _TreeMeta:
    """`input_hash` — 트리 내용만으로 정해지는 해시.

    `engine.py`의 기존 스냅샷 해시(ZIP=zip_bytes 자체, GITHUB_URL=스캐너-필터링된
    파일만)와 다른 별도 정의다 -- 그 해시는 vendor 스캐너가 바뀔 때마다(흔함,
    `extractor_version()`이 존재하는 이유) 코드 한 줄 안 바뀌어도 값이 달라진다.

    여기서는 스캔 루트 아래 `.git/**`를 제외한 전 파일을, 상대경로 정렬 순서로
    `경로바이트 + NUL + 원본바이트`를 이어붙여 SHA-256을 낸다. 같은 트리면 ZIP으로
    받든 클론으로 받든 동일한 해시가 나온다 -- 재제출 판별에 쓸 수 있다.
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
