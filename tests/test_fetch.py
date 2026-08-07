""" 분석 입력 fetch(analysis-inputs 분리)의 검증·해시·git 로그 파싱·failureCode 분류.

**네트워크가 필요한 것은 여기 없다.** 실제 GitHub 클론/재fetch·presigned URL 다운로드는
PR 본문의 수동 체크리스트로 확인한다(계획 §검증) — 여기는 순수 로직 + 로컬 git 저장소만
쓴다.
"""
import io
import subprocess
import zipfile
from pathlib import Path

import pytest

from app.engines.analysis import fetch, rules


def _init_local_repo(path: Path) -> None:
    """네트워크 없이 로컬 git 저장소 하나를 만든다(fetch.git_env 재사용, D12 안전).

    `origin`이 없다 -- `git log`/`rev-parse`(임베디드 `.git` 파싱, `_head_commit`)는
    remote 없이도 잘 되므로 이걸로 충분하다. `origin`이 실제로 필요한 "shallow-since
    성공 경로"는 `GIT_ALLOW_PROTOCOL=http:https`(D12)가 로컬 `file://` origin까지
    거부해서 오프라인 유닛 테스트로 못 만든다(실측 확인) -- 그 경로는 실네트워크
    체크리스트에서 검증한다(아래 주석 참고).
    """
    from app.engines.analysis import materialize

    env = materialize.git_env()
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "a@b.c"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True, env=env)


def _commit(path: Path, filename: str, content: str, message: str) -> None:
    from app.engines.analysis import materialize

    env = materialize.git_env()
    (path / filename).write_text(content)
    subprocess.run(["git", "-C", str(path), "add", filename], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True, env=env)


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


# ── 입력 검증(flag smuggling 등) ───────────────────────────────────────────


@pytest.mark.parametrize("repo_url", [
    "ext::sh -c 'touch pwned'",     # ext:: 서브프로토콜은 임의 명령 실행이다
    "file:///etc/passwd",
    "git://github.com/o/r.git",
    "not-a-url",
])
def test_dangerous_repo_url_is_rejected(repo_url):
    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_github({"repository_url": repo_url}, "/tmp/unused")
    assert exc.value.failure_code == "INVALID_REPOSITORY_URL"


def test_branch_that_looks_like_an_option_is_rejected():
    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_github(
            {"repository_url": "https://github.com/owner/repo",
             "requested_branch": "--upload-pack=touch pwned"},
            "/tmp/unused",
        )
    assert exc.value.failure_code == "BRANCH_NOT_FOUND"


def test_missing_repo_url_is_rejected():
    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_github({}, "/tmp/unused")
    assert exc.value.failure_code == "INVALID_REPOSITORY_URL"


def test_unsupported_host_is_rejected():
    """UNSUPPORTED_HOST -- 지금 이 서비스는 github.com만 허용한다(allowlist 신규)."""
    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_github({"repository_url": "https://gitlab.com/owner/repo"}, "/tmp/unused")
    assert exc.value.failure_code == "UNSUPPORTED_HOST"


def test_empty_repo_host_allowlist_is_fail_closed(monkeypatch):
    """🔴 허용목록이 비면 검사를 건너뛰는 게 아니라 거부해야 한다.

    옛 `if allowed and host not in allowed`는 ALLOWED_REPO_HOSTS가 빈 값이면
    아무 호스트나 clone했다 -- 설정 실수 하나가 방어를 통째로 지웠다.
    """
    monkeypatch.setattr(fetch, "_allowed_repo_hosts", set)

    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_github({"repository_url": "https://evil.example/owner/repo"}, "/tmp/unused")
    assert exc.value.failure_code == "UNSUPPORTED_HOST"


@pytest.mark.parametrize("sha", [
    "not-a-sha",
    "-rm-rf",           # 옵션으로 오인될 수 있는 값 -- D12와 같은 클래스의 방어 대상
    "a" * 39,           # 40자 미만
    "g" * 40,           # 40자지만 hex 아님
])
def test_malformed_pinned_sha_is_rejected(sha):
    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_github_pinned(
            {"repository_url": "https://github.com/owner/repo"}, "/tmp/unused", sha,
        )
    assert exc.value.failure_code == "INVALID_REPOSITORY_URL"


def test_zip_without_download_url_is_rejected():
    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_zip({}, "/tmp/unused")
    assert exc.value.failure_code == "ARCHIVE_INVALID"


def test_zip_s3_uri_fails_fast_not_silently():
    """이 서비스엔 boto3/AWS 자격증명이 전혀 없다(requirements.txt 확인 완료) --

    s3://를 조용히 500내는 대신 즉시, 명확한 사유로 거부한다."""
    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_zip({"storage_uri": "s3://bucket/key.zip"}, "/tmp/unused")
    assert exc.value.failure_code == "ARCHIVE_INVALID"
    assert "s3" in exc.value.message.lower()


def test_download_url_rejects_non_http_scheme():
    with pytest.raises(fetch.FetchError) as exc:
        fetch._download("ftp://example.com/x.zip")
    assert exc.value.failure_code == "ARCHIVE_INVALID"


def test_download_rejects_when_no_storage_host_allowlisted():
    """기본값이 빈 문자열(fail-closed)이라 설정 안 하면 모든 다운로드를 거부해야 한다."""
    with pytest.raises(fetch.FetchError) as exc:
        fetch._download("https://anywhere.example.com/x.zip")
    assert exc.value.failure_code == "ARCHIVE_INVALID"


# ── input_hash 결정성(D2 무결성 검사의 기반) ────────────────────────────────


def test_input_hash_is_deterministic_for_the_same_tree(tmp_path):
    files = {"src/main.py": b"print(1)\n", "readme.md": b"hi\n"}
    a, b = tmp_path / "a", tmp_path / "b"
    _write_tree(a, files)
    _write_tree(b, files)

    assert fetch._hash_tree(str(a)).hash == fetch._hash_tree(str(b)).hash


def test_input_hash_changes_when_one_byte_changes(tmp_path):
    a = tmp_path / "a"
    _write_tree(a, {"src/main.py": b"print(1)\n"})
    before = fetch._hash_tree(str(a)).hash

    (a / "src/main.py").write_bytes(b"print(2)\n")
    after = fetch._hash_tree(str(a)).hash

    assert before != after


def test_input_hash_ignores_dot_git_contents(tmp_path):
    """vendor 스캐너 drift와 달리, .git 안의 내용은 코드가 아니므로 해시에서 아예 뺀다."""
    a = tmp_path / "a"
    _write_tree(a, {"src/main.py": b"print(1)\n"})
    before = fetch._hash_tree(str(a)).hash

    _write_tree(a, {".git/config": b"[core]\n\tfsmonitor = touch pwned\n"})
    after = fetch._hash_tree(str(a)).hash

    assert before == after


def test_input_hash_same_for_zip_and_directory_with_same_content(tmp_path):
    """백엔드의 '같은 inputHash면 analysisInputId 재사용' 요청 -- ZIP과 클론이 같은

    트리면 같은 해시가 나와야 그 재사용이 실제로 성립한다(기존 스냅샷 해시는
    ZIP=zip_bytes 자체/GITHUB_URL=필터링된 파일이라 서로 달랐던 문제, D2에서 재정의)."""
    files = {"src/main.py": b"print(1)\n", "readme.md": b"hi\n"}

    directory = tmp_path / "dir"
    _write_tree(directory, files)
    dir_hash = fetch._hash_tree(str(directory)).hash

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    rules._safe_extract(buf.getvalue(), extracted)
    zip_hash = fetch._hash_tree(str(extracted)).hash

    assert dir_hash == zip_hash


# ── D2 -- 재fetch 무결성 검사 ────────────────────────────────────────────


def test_refetch_pinned_rejects_hash_mismatch(monkeypatch, tmp_path):
    """재fetch한 코드가 검증했던 것과 다르면(브랜치가 그 사이 바뀌었으면) 하드 실패해야 한다."""
    fake_root = tmp_path / "refetched"
    _write_tree(fake_root, {"x.txt": b"different content\n"})
    fake_meta = fetch._hash_tree(str(fake_root))

    def fake_fetch_github_pinned(descriptor, tmp, sha):
        return fetch.FetchedInput(
            root=str(fake_root), method="GITHUB_URL", resolved_branch="main",
            head_commit={"sha": sha, "message": "m", "committed_at": "2026-01-01T00:00:00Z"},
            input_hash=fake_meta.hash, file_count=fake_meta.file_count,
            byte_count=fake_meta.byte_count,
        )

    monkeypatch.setattr(fetch, "_fetch_github_pinned", fake_fetch_github_pinned)

    descriptor = {
        "method": "GITHUB_URL", "repository_url": "https://github.com/owner/repo",
        "head_commit_sha": "a" * 40,
        "input_hash": "0" * 64,  # 실제(fake_meta.hash)와 다른 값
    }
    with pytest.raises(fetch.FetchError) as exc:
        with fetch.refetch_pinned(descriptor):
            pass
    assert exc.value.failure_code == "INPUT_HASH_MISMATCH"


def test_refetch_pinned_accepts_matching_hash(monkeypatch, tmp_path):
    fake_root = tmp_path / "refetched"
    _write_tree(fake_root, {"x.txt": b"same content\n"})
    fake_meta = fetch._hash_tree(str(fake_root))

    def fake_fetch_github_pinned(descriptor, tmp, sha):
        return fetch.FetchedInput(
            root=str(fake_root), method="GITHUB_URL", resolved_branch="main",
            head_commit={"sha": sha, "message": "m", "committed_at": "2026-01-01T00:00:00Z"},
            input_hash=fake_meta.hash, file_count=fake_meta.file_count,
            byte_count=fake_meta.byte_count,
        )

    monkeypatch.setattr(fetch, "_fetch_github_pinned", fake_fetch_github_pinned)

    descriptor = {
        "method": "GITHUB_URL", "repository_url": "https://github.com/owner/repo",
        "head_commit_sha": "a" * 40,
        "input_hash": fake_meta.hash,
    }
    with fetch.refetch_pinned(descriptor) as result:
        assert result.input_hash == fake_meta.hash


# ── D1 -- 히스토리 수집 실패가 코드 fetch에 영향 없어야 함 ─────────────────


def test_history_enrichment_degrades_gracefully_on_failure(monkeypatch, tmp_path):
    """Phase B(--shallow-since)가 실패해도 예외를 던지지 않고 NONE으로 내려가야 한다.

    (실측 확인: octocat/Hello-World처럼 히스토리 윈도우 밖인 레포는 실제로 이 경로를
    탄다 -- `git fetch --shallow-since`가 "error processing shallow info"로 실패함.)
    """
    repo_dir = tmp_path / "repo"
    _init_local_repo(repo_dir)
    _commit(repo_dir, "f.txt", "hello", "chore: init")

    real_run = fetch.subprocess.run

    def flaky_run(cmd, *args, **kwargs):
        if "fetch" in cmd and "--shallow-since" in cmd:
            raise subprocess.TimeoutExpired(cmd, 3)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(fetch.subprocess, "run", flaky_run)

    history, truncated, source = fetch._try_deepen_history(str(repo_dir))
    assert history == []
    assert truncated is False
    assert source == "NONE"


# 🔴 "shallow-since 성공 경로"는 순수 오프라인 유닛 테스트로 못 만든다 -- 시도해보니
# `_try_deepen_history`가 부르는 `git fetch --shallow-since ... origin`이
# `GIT_ALLOW_PROTOCOL=http:https`(D12)에 걸려 로컬 `file://` origin은 거부된다(실측
# 확인, 2026-08-06). 이건 버그가 아니라 그 방어가 로컬 클론에도 일관되게 적용된다는
# 뜻이라 오히려 좋은 신호다 -- 대신 이 성공 경로는 실네트워크로만 검증 가능하다.
# 이미 이번 세션에서 실제 공개 GitHub 저장소(octocat/Hello-World, GIT_HISTORY_SINCE_DAYS
# 를 6000으로 넓혀서)로 직접 확인했다: 커밋 2개, author_name/author_email/changed_files/
# additions/deletions 전부 정확히 파싱됨. PR 본문 수동 체크리스트에 반복 검증 항목으로 남긴다.


# ── git log 파서(subprocess 없이 원문 텍스트만) ────────────────────────────


def test_parse_git_log_output_handles_multiple_commits_and_merges():
    RS, FS = fetch._RS, fetch._FS
    raw = (
        f"{RS}aaa1{FS}p-aaa0{FS}Alice{FS}alice@x.com{FS}2026-01-01T00:00:00+09:00"
        f"{FS}2026-01-01T09:00:00+09:00{FS}fix bug\n"
        "3\t1\tsrc/main.py\n"
        "0\t0\tREADME.md\n"
        f"{RS}bbb2{FS}p1 p2{FS}Bob{FS}bob@x.com{FS}2026-01-02T00:00:00+09:00"
        f"{FS}2026-01-02T09:00:00+09:00{FS}Merge pull request #1\n"  # merge(부모 2개) -- numstat 없음
        f"{RS}ccc3{FS}p-ccc2{FS}Carol With Space{FS}carol@x.com{FS}2026-01-03T00:00:00+09:00"
        f"{FS}2026-01-03T09:00:00+09:00{FS}add feature\n"
        "1\t0\tsrc/a file with space.py\n"
    )
    commits = fetch._parse_git_log_output(raw)

    assert len(commits) == 3
    assert commits[0] == {
        "sha": "aaa1", "author_name": "Alice", "author_email": "alice@x.com",
        "committed_at": "2026-01-01T09:00:00+09:00",
        "changed_files": ["src/main.py", "README.md"],
        "additions": 3, "deletions": 1,
        "authored_at": "2026-01-01T00:00:00+09:00",
        "parent_sha": "p-aaa0",
        "is_merge_commit": False,
        "is_revert_commit": False,
        "is_bot_commit": False,
        "changed_line_count": 4,
    }
    # 🔴 merge 커밋은 changedFiles/additions/deletions가 조용히 빈값이 된다(문서화된 동작).
    assert commits[1]["changed_files"] == []
    assert commits[1]["additions"] == 0
    assert commits[1]["is_merge_commit"] is True
    assert commits[1]["parent_sha"] == "p1"  # 첫 부모(mainline) 기준
    assert commits[2]["author_name"] == "Carol With Space"
    assert commits[2]["changed_files"] == ["src/a file with space.py"]
    assert commits[2]["is_merge_commit"] is False


def test_parse_git_log_output_root_commit_has_no_parent():
    """부모 없는 root 커밋은 parentSha가 all-zero sentinel(NOT NULL 컬럼 대응)."""
    RS, FS = fetch._RS, fetch._FS
    raw = f"{RS}root1{FS}{FS}Alice{FS}alice@x.com{FS}2026-01-01T00:00:00+09:00{FS}2026-01-01T00:00:00+09:00{FS}init\n"
    commits = fetch._parse_git_log_output(raw)

    assert len(commits) == 1
    assert commits[0]["parent_sha"] == "0" * 40
    assert commits[0]["is_merge_commit"] is False


def test_parse_git_log_output_detects_revert_commit():
    RS, FS = fetch._RS, fetch._FS
    raw = (
        f'{RS}rev1{FS}p1{FS}Alice{FS}alice@x.com{FS}2026-01-01T00:00:00+09:00'
        f'{FS}2026-01-01T00:00:00+09:00{FS}Revert "add flaky feature"\n'
    )
    commits = fetch._parse_git_log_output(raw)

    assert commits[0]["is_revert_commit"] is True


def test_parse_git_log_output_detects_bot_commit_by_email_or_name():
    RS, FS = fetch._RS, fetch._FS
    raw = (
        f"{RS}bot1{FS}p1{FS}dependabot[bot]{FS}49699333+dependabot[bot]@users.noreply.github.com"
        f"{FS}2026-01-01T00:00:00+09:00{FS}2026-01-01T00:00:00+09:00{FS}bump deps\n"
        f"{RS}bot2{FS}p1{FS}Custom CI Bot{FS}ci-bot@example.com"
        f"{FS}2026-01-01T00:00:00+09:00{FS}2026-01-01T00:00:00+09:00{FS}deploy\n"
        f"{RS}human1{FS}p1{FS}Alice{FS}alice@x.com"
        f"{FS}2026-01-01T00:00:00+09:00{FS}2026-01-01T00:00:00+09:00{FS}fix\n"
    )
    commits = fetch._parse_git_log_output(raw)

    assert commits[0]["is_bot_commit"] is True   # GitHub App bot noreply 이메일
    # 대괄호 없는 커스텀 서비스 계정은 의도적으로 범위 밖(scope-out, D-analysis-b1)
    assert commits[1]["is_bot_commit"] is False
    assert commits[2]["is_bot_commit"] is False


def test_parse_git_log_output_empty_input():
    assert fetch._parse_git_log_output("") == []


# ── failureCode 매핑(11개 전수) ────────────────────────────────────────────


@pytest.mark.parametrize("stderr,expected_code,expected_retryable", [
    (b"fatal: repository 'https://github.com/x/y' not found", "REPO_NOT_FOUND", False),
    (b"fatal: Authentication failed for 'https://github.com/private/repo.git/'",
     "REPOSITORY_ACCESS_DENIED", False),
    (b"fatal: Remote branch nope not found in upstream origin", "BRANCH_NOT_FOUND", False),
    (b"fatal: unable to access '...': Could not resolve host: github.com",
     "TEMPORARY_ERROR", True),
    (b"fatal: unable to access '...': Connection timed out", "TEMPORARY_ERROR", True),
    (b"something completely unrecognized", "TEMPORARY_ERROR", True),
])
def test_classify_github_error(stderr, expected_code, expected_retryable):
    err = fetch._classify_github_error(stderr)
    assert err.failure_code == expected_code
    assert err.retryable is expected_retryable


def test_classify_github_error_ambiguous_not_found_defaults_conservatively():
    """GitHub은 비공개/존재안함을 의도적으로 구분 안 해준다(개인정보 보호) -- 토큰 없이는

    실제로 구분 불가능하다(계획에 이미 명시된 한계). "not found"가 있으면 access-denied
    문구가 같이 있어도 REPO_NOT_FOUND로 떨어지는 게 지금 구현의 실제 동작이다."""
    err = fetch._classify_github_error(
        b"remote: Repository not found.\nfatal: Authentication failed"
    )
    assert err.failure_code == "REPO_NOT_FOUND"


def test_failure_code_vocabulary_is_pinned():
    """11개 값의 정확한 집합을 고정한다 -- 백엔드 실제 배포본이 이 세션의 feature_code

    사례처럼 문서와 어긋나면, 여기가 먼저 깨져서 알려준다."""
    assert fetch.GITHUB_FAILURE_CODES == {
        "INVALID_REPOSITORY_URL", "REPO_NOT_FOUND", "REPOSITORY_ACCESS_DENIED",
        "BRANCH_NOT_FOUND", "UNSUPPORTED_HOST", "TEMPORARY_ERROR",
    }
    assert fetch.ZIP_FAILURE_CODES == {
        "FILE_TOO_LARGE", "ARCHIVE_INVALID", "EMPTY_CODE", "PROHIBITED_FILE",
        "GIT_LOG_MISSING",
    }
    assert len(fetch.VERIFICATION_FAILURE_CODES) == 11


def test_stderr_redaction_strips_credentials_and_tokens():
    """githubInstallationId(자격증명)가 도입되면 stderr가 job.failure_reason으로

    Spring DB에 그대로 남는다 -- 지금부터 마스킹해 둔다."""
    leaked = (
        "fatal: unable to access 'https://x-access-token:ghp_"
        + "A" * 30
        + "@github.com/org/repo/': The requested URL returned error: 403"
    )
    redacted = fetch._redact(leaked)
    assert "ghp_" not in redacted
    assert "x-access-token" not in redacted
    assert "github.com/org/repo" in redacted  # 진단에 필요한 정보는 남긴다


# ── D-git-rce -- 임베디드 .git 하드닝(가장 중요한 테스트) ──────────────────


def _make_malicious_embedded_git(root: Path, marker: Path) -> None:
    """fsmonitor + textconv 두 벡터를 동시에 심은 저장소.

    2026-08-06 실측 확인: `core.fsmonitor`는 `git log`/`rev-parse`에서 발동 안 하지만
    (그건 `git status`·`add`·`commit`처럼 작업트리 상태를 보는 명령 전용이다),
    `.gitattributes`의 `diff=X` + `.git/config`의 `[diff "X"] textconv=...`는 `-p`
    없이 `--numstat`만 줘도 발동한다 -- 이게 이 코드의 실제 호출 패턴(`git log
    --numstat`)에 대한 진짜 공격면이다.

    🔴 **모든 커밋을 먼저 끝내고, 악의적 설정은 맨 마지막에 딱 한 번만 주입한다.**
    순서를 반대로 하면(설정 넣고 나서 또 `git add`/`commit`을 부르면) 그 커밋 자체가
    `git add`/`commit`이라 fsmonitor를 발동시켜서 **테스트 셋업 코드 스스로가 익스플로잇을
    터뜨려 버린다**(처음 이 테스트를 작성했을 때 실제로 겪은 실수).
    """
    _init_local_repo(root)
    _commit(root, "f.txt", "hello", "chore: init")
    (root / ".gitattributes").write_text("*.txt diff=evil\n")
    _commit(root, ".gitattributes", "*.txt diff=evil\n", "chore: attributes")
    _commit(root, "f.txt", "hello v2", "chore: modify")

    # 여기서부터는 git을 다시 부르지 않는다 -- config 파일을 직접 텍스트로 추가할 뿐이다.
    config_path = root / ".git" / "config"
    with config_path.open("a") as f:
        f.write(f'[core]\n\tfsmonitor = touch {marker}\n')
        f.write(f'[diff "evil"]\n\ttextconv = touch {marker}; cat\n')


def test_embedded_git_history_blocks_fsmonitor_and_textconv_rce(tmp_path):
    """가장 중요한 신규 테스트 -- 학생이 ZIP에 넣은 `.git/config`가 임의 명령을

    실행하면 안 된다. `_try_embedded_git_history`를 통해서만 접근했을 때 마커 파일이
    생기지 않아야 하고, 그러면서도 정당한 히스토리(3커밋)는 정상적으로 뽑혀야 한다."""
    root = tmp_path / "malicious-repo"
    marker = tmp_path / "pwned_marker"
    _make_malicious_embedded_git(root, marker)
    assert not marker.exists()  # 설정만 심었지 아직 아무 git 명령도 안 불렀다

    history, source = fetch._try_embedded_git_history(str(root))

    assert not marker.exists(), "RCE 벡터가 발동했습니다 -- .git/config 하드닝이 깨짐"
    assert source == "EMBEDDED_GIT"
    assert len(history) == 3
    assert not (root / ".git" / "config").exists(), "위험 설정 파일이 안 지워졌습니다"


def test_embedded_git_history_returns_none_when_no_git_dir(tmp_path):
    root = tmp_path / "plain"
    _write_tree(root, {"a.py": b"x = 1\n"})
    history, source = fetch._try_embedded_git_history(str(root))
    assert history == []
    assert source == "NONE"


def test_zip_fetch_uses_backend_supplied_history_over_embedded(monkeypatch, tmp_path):
    """D3 우선순위 ① -- 백엔드가 히스토리를 실어 보내면, ZIP 안에 (설령 악의적인)

    `.git`이 있어도 그걸 파싱하지 않고 백엔드 값을 그대로 쓴다. `_download`는
    네트워크를 타므로 여기서는 zip bytes를 직접 만들어 monkeypatch로 대체한다.
    """
    root = tmp_path / "repo-with-git"
    marker = tmp_path / "unused_marker"
    _make_malicious_embedded_git(root, marker)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(fetch, "_download", lambda url: zip_bytes)

    backend_history = [{"sha": "z9", "author_name": "Backend", "author_email": "b@x.com",
                        "committed_at": "2026-01-01T00:00:00Z", "changed_files": [],
                        "additions": 0, "deletions": 0}]

    tmp_dir = tmp_path / "extract-target"
    tmp_dir.mkdir()
    result = fetch._fetch_zip(
        {"download_url": "https://example.com/x.zip", "git_history": backend_history},
        str(tmp_dir),
    )

    assert result.git_history_source == "BACKEND_SUPPLIED"
    assert result.git_history == backend_history
    assert not marker.exists()  # 임베디드 .git을 아예 안 건드렸으니 발동할 일도 없다


def test_zip_fetch_falls_back_to_embedded_git_when_backend_silent(monkeypatch, tmp_path):
    """D3 우선순위 ② -- 백엔드가 히스토리를 안 주면 ZIP 안의 `.git`을 직접 파싱한다."""
    root = tmp_path / "repo-with-git"
    marker = tmp_path / "unused_marker2"
    _init_local_repo(root)
    _commit(root, "f.txt", "hello", "chore: init")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(fetch, "_download", lambda url: zip_bytes)

    tmp_dir = tmp_path / "extract-target"
    tmp_dir.mkdir()
    result = fetch._fetch_zip({"download_url": "https://example.com/x.zip"}, str(tmp_dir))

    assert result.git_history_source == "EMBEDDED_GIT"
    assert len(result.git_history) == 1


def test_zip_fetch_degrades_to_none_when_neither_source_available(monkeypatch, tmp_path):
    """D3 우선순위 ③ -- 백엔드도 안 주고 .git도 없으면 실패시키지 않고 빈 값으로 진행한다

    (기본 설정 `zip_require_git_log=False`)."""
    root = tmp_path / "plain-repo"
    _write_tree(root, {"f.txt": b"hello\n"})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("f.txt", "hello\n")
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(fetch, "_download", lambda url: zip_bytes)

    tmp_dir = tmp_path / "extract-target"
    tmp_dir.mkdir()
    result = fetch._fetch_zip({"download_url": "https://example.com/x.zip"}, str(tmp_dir))

    assert result.git_history_source == "NONE"
    assert result.git_history == []
    assert result.file_count == 1


def test_download_aborts_past_the_size_cap(monkeypatch):
    """🔴 상한 검사는 다 받은 뒤가 아니라 받는 도중이어야 한다.

    옛 코드는 httpx.get()으로 본문 전체를 메모리에 받은 뒤 len()을 쟀다 -- 상한
    검사 줄에 도달하기 전에 프로세스가 죽는다(App Runner 단일 인스턴스라 다른
    요청도 같이 죽는다). 여기서는 상한의 3배를 흘려보내되, 실제로 소비된 양이
    상한 근처에서 멈췄는지까지 본다(끊지 않으면 전량이 소비된다).
    """
    chunk = b"x" * 1024
    total_chunks = (rules.MAX_TOTAL_BYTES // len(chunk)) * 3
    consumed = {"chunks": 0}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def iter_bytes(self):
            for _ in range(total_chunks):
                consumed["chunks"] += 1
                yield chunk

    class _FakeStream:
        def __enter__(self):
            return _FakeResponse()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(fetch.httpx, "stream", lambda *a, **kw: _FakeStream())
    monkeypatch.setattr(
        fetch, "get_settings",
        lambda: type("S", (), {"allowed_storage_hosts": "storage.example",
                               "analysis_input_clone_timeout_s": 10})(),
    )

    with pytest.raises(fetch.FetchError) as exc:
        fetch._download("https://storage.example/x.zip")

    assert exc.value.failure_code == "FILE_TOO_LARGE"
    assert consumed["chunks"] * len(chunk) <= rules.MAX_TOTAL_BYTES + len(chunk)


def test_empty_repo_is_reported_as_empty_code(monkeypatch, tmp_path):
    """빈 클론이 검증을 통과하면 실패가 한참 뒤 분석 단계에서 다른 사유로 나온다.

    ZIP 경로(_fetch_zip)는 이미 EMPTY_CODE를 내고 있었다 -- GITHUB_URL만 빠져 있었다.
    """
    empty = tmp_path / "empty-clone"
    empty.mkdir()

    monkeypatch.setattr(fetch.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(fetch, "_current_branch", lambda repo_dir: "main")
    monkeypatch.setattr(fetch, "_head_commit", lambda repo_dir: None)
    monkeypatch.setattr(fetch, "_try_deepen_history", lambda repo_dir: ([], False, "NONE"))

    with pytest.raises(fetch.FetchError) as exc:
        fetch._fetch_github({"repository_url": "https://github.com/owner/repo"}, str(empty))
    assert exc.value.failure_code == "EMPTY_CODE"
