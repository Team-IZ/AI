"""Phase 2a — 분석 API(P02) 테스트 (명세 §3).

네트워크 없이 도는 것만 기본 수집 대상이다. GitHub clone 경로는 외부 의존이라
`--run-network` 없이는 skip 한다(맨 아래).
"""
import io
import json
import re
import subprocess
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api.deps import INTERNAL_KEY_HEADER
from app.config import API_V1_PREFIX, get_settings
from app.core import collect, findings as findings_mod
from app.main import create_app

ANALYSES = f"{API_V1_PREFIX}/analyses"


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """작업공간을 테스트별 tmp_path로 격리한다 (§3.3: 원문은 작업공간에만)."""
    monkeypatch.setenv("APP_MODE", "integrated")
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


SAMPLE_REPO = {
    "src/utils.py": "def helper(x):\n    return x * 2\n",
    "src/core.py": "import utils\n\n\ndef run(v):\n    return utils.helper(v)\n",
    "src/main.py": "import utils\nimport core\n\nprint(core.run(21))\n",
    "src/danger.py": (
        "import utils\n\n"
        'API_KEY = "sk-abcdef1234567890abcdef1234567890"\n\n\n'
        "def unsafe(expr):\n    return eval(expr)\n"
    ),
    "README.md": "# 문서 — 소스 아님\n",
    "node_modules/junk.js": "module.exports = 1;\n",
}


def _submit_zip(client, entries, payload=None, **kwargs):
    payload = payload or {"method": "ZIP_WITH_GITLOG"}
    return client.post(
        ANALYSES,
        data={"payload": json.dumps(payload)},
        files={"file": ("submission.zip", _zip_bytes(entries), "application/zip")},
        **kwargs,
    )


def _await_job(client, job_id):
    """BackgroundTasks는 TestClient에서 응답 반환 직후 동기 실행되지만,
    Spring이 실제로 쓰는 경로(폴링)를 그대로 밟아 확인한다."""
    for _ in range(50):
        res = client.get(f"{ANALYSES}/{job_id}")
        assert res.status_code == 200
        body = res.json()
        if body["status"] in ("READY", "PARTIAL", "FAILED"):
            return body
    raise AssertionError("job이 종료 상태에 도달하지 않았다")


# --- E2E: 제출 → job → 폴링 → finding/code_context 확인 -----------------------


def test_zip_submission_end_to_end(client):
    res = _submit_zip(
        client,
        SAMPLE_REPO,
        {
            "attempt_id": "att-1",
            "submission_id": "sub-1",
            "method": "ZIP_WITH_GITLOG",
            "extraction_scope": "TOTAL",
            "question_budget": 4,
        },
    )
    # §3.1: 202 + {job_id, status: QUEUED}
    assert res.status_code == 202
    accepted = res.json()
    assert accepted["status"] == "QUEUED"
    job_id = accepted["job_id"]

    body = _await_job(client, job_id)

    assert body["status"] == "READY"
    assert body["failure_reason"] is None
    # §2: Spring이 부여한 도메인 키를 그대로 에코
    assert body["attempt_id"] == "att-1"
    assert body["submission_id"] == "sub-1"
    # P02는 LLM 호출이 없다
    assert body["ai_usage"] == []

    result = body["result"]
    assert result["applied_scope"] == "TOTAL"
    assert result["scope_fallback"] is False
    assert result["fallback_reason"] is None
    assert result["attribution"] is None  # TOTAL이므로 없음
    assert result["commit_sha"] is None  # ZIP 제출이므로 없음

    findings = result["findings"]
    assert findings, "샘플 레포에서 finding이 최소 1건 나와야 한다"
    assert result["question_count_planned"] == min(4, len(findings))

    # §3.3의 핵심: finding마다 code_context가 있어야 한다
    for f in findings:
        assert f["finding_id"]
        assert f["type"] == "CODE"
        ctx = f["code_context"]
        assert ctx is not None, f"code_context 누락: {f['finding_id']}"
        assert ctx["path"] and ctx["snippet"]
        assert 1 <= ctx["line_start"] <= ctx["line_end"]
        assert f["evidence_hash"]

    # 시크릿 finding의 code_context에 실제 근거 줄이 들어 있어야 한다
    secret = [f for f in findings if "secret" in (f["finding_id"] or "")]
    assert secret, "시크릿 finding이 검출돼야 한다"
    assert "API_KEY" in secret[0]["code_context"]["snippet"]
    assert secret[0]["source_path"] == "src/danger.py"
    # 같은 파일의 서로 다른 finding이 같은 발췌 창을 공유해도 해시는 구분돼야 한다
    assert len({f["evidence_hash"] for f in findings}) == len(findings)


# --- S1: snapshot_id / snapshot_meta (M3 무저장 + 메타 제공, N1 별도 UUID) -----


def test_ready_result_contains_snapshot_id_and_meta(client):
    """READY 응답의 result에 snapshot_id(UUID, job_id와 별개)와 snapshot_meta가 있다."""
    job_id = _submit_zip(client, SAMPLE_REPO).json()["job_id"]
    body = _await_job(client, job_id)
    assert body["status"] == "READY"
    result = body["result"]

    snapshot_id = result["snapshot_id"]
    assert snapshot_id
    # UUID 형식 검증 (N1: 별도 발급 — job_id와 달라야 한다)
    uuid.UUID(snapshot_id)
    assert snapshot_id != job_id

    meta = result["snapshot_meta"]
    assert re.fullmatch(r"[0-9a-f]{64}", meta["content_hash"]), "sha256 hex 64자여야 한다"
    assert meta["file_count"] > 0
    assert meta["byte_count"] > 0


def test_snapshot_content_hash_is_deterministic(client):
    """같은 제출물이면 content_hash는 동일해야 하고(무결성 비교 취지),
    snapshot_id는 발급 단위가 분석이므로 서로 달라야 한다."""
    first = _await_job(client, _submit_zip(client, SAMPLE_REPO).json()["job_id"])
    second = _await_job(client, _submit_zip(client, SAMPLE_REPO).json()["job_id"])
    r1, r2 = first["result"], second["result"]
    assert r1["snapshot_meta"]["content_hash"] == r2["snapshot_meta"]["content_hash"]
    assert r1["snapshot_meta"] == r2["snapshot_meta"]
    assert r1["snapshot_id"] != r2["snapshot_id"]


def test_skipped_paths_are_not_analyzed(client):
    """SKIP_DIR_NAMES·비소스 확장자는 수집 대상에서 빠진다."""
    body = _await_job(client, _submit_zip(client, SAMPLE_REPO).json()["job_id"])
    paths = {f["source_path"] for f in body["result"]["findings"]}
    assert not any(p and "node_modules" in p for p in paths)
    assert not any(p and p.endswith(".md") for p in paths)


# --- 인증 -------------------------------------------------------------------


def test_requires_internal_key(monkeypatch):
    """B1: 키가 설정된 배포에서 인증 없이 호출하면 거부된다."""
    monkeypatch.setenv("INTERNAL_API_KEY", "shared-secret")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        no_key = c.post(ANALYSES, json={"method": "GITHUB_URL", "source": {"repo_url": "x"}})
        assert no_key.status_code == 401
        assert no_key.json()["detail"]["error"]["code"] == "INTERNAL_KEY_MISSING"

        get_res = c.get(f"{ANALYSES}/whatever")
        assert get_res.status_code == 401

        ok = _submit_zip(c, SAMPLE_REPO, headers={INTERNAL_KEY_HEADER: "shared-secret"})
        assert ok.status_code == 202


# --- 실패 경로 ---------------------------------------------------------------


def test_zip_without_source_files_fails_with_no_source(client):
    """분석 대상 소스가 0개면 NO_SOURCE로 실패한다."""
    body = _await_job(
        client,
        _submit_zip(client, {"README.md": "# 문서만 있음\n", "data.csv": "a,b\n"}).json()["job_id"],
    )
    assert body["status"] == "FAILED"
    assert body["failure_reason"] == "NO_SOURCE"
    assert body["result"] is None
    # §2 공통 에러 형식 — retryable 포함
    assert body["error"]["code"] == "NO_SOURCE"
    assert body["error"]["retryable"] is False


def test_own_commit_without_any_git_log_falls_back_to_total(client):
    """MEAS-02A A-2: `.git`도 export도 없으면 TOTAL 폴백 + scope_fallback=true."""
    body = _await_job(
        client,
        _submit_zip(
            client,
            SAMPLE_REPO,
            {
                "method": "ZIP_WITH_GITLOG",
                "extraction_scope": "OWN_COMMIT",
                "commit_email": "trainee@example.com",
            },
        ).json()["job_id"],
    )
    assert body["status"] == "PARTIAL"
    result = body["result"]
    assert result["applied_scope"] == "TOTAL"
    assert result["scope_fallback"] is True
    assert result["fallback_reason"] == "NO_COMMIT_LOG"


def test_own_commit_with_no_matching_commits_requires_attribution(client):
    """MEAS-02A A-1: 로그는 있으나 본인 커밋 0건 → ATTRIBUTION_REQUIRED."""
    entries = dict(SAMPLE_REPO)
    entries["commits.txt"] = (
        "commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "Author: 다른사람 <someone-else@example.com>\n"
        "Date: 2026-07-01\n\n    첫 커밋\n"
    )
    entries["changed_files.txt"] = "someone-else@example.com\nsrc/core.py\n"

    body = _await_job(
        client,
        _submit_zip(
            client,
            entries,
            {
                "method": "ZIP_WITH_GITLOG",
                "extraction_scope": "OWN_COMMIT",
                "commit_email": "trainee@example.com",
            },
        ).json()["job_id"],
    )
    assert body["status"] == "FAILED"
    assert body["failure_reason"] == "ATTRIBUTION_REQUIRED"


def test_own_commit_scopes_to_attributed_files(client):
    """동봉 export가 본인 커밋을 가리키면 그 파일들만 분석 대상이 된다."""
    entries = dict(SAMPLE_REPO)
    entries["commits.txt"] = (
        "commit bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        "Author: 교육생 <trainee@example.com>\n"
        "Date: 2026-07-02\n\n    위험 코드 추가\n"
    )
    entries["changed_files.txt"] = "trainee@example.com\nsrc/danger.py\n"

    body = _await_job(
        client,
        _submit_zip(
            client,
            entries,
            {
                "method": "ZIP_WITH_GITLOG",
                "extraction_scope": "OWN_COMMIT",
                "commit_email": "trainee@example.com",
            },
        ).json()["job_id"],
    )
    assert body["status"] == "READY"
    result = body["result"]
    assert result["applied_scope"] == "OWN_COMMIT"
    assert result["scope_fallback"] is False
    assert result["attribution"]["commit_count"] == 1
    # 교육생이 만든 텍스트 export이므로 서버가 위변조를 검증할 수 없다 (AUTH-07)
    assert result["attribution"]["verification_status"] == "UNVERIFIED"
    for f in result["findings"]:
        assert f["source_path"] in (None, "src/danger.py")


def test_own_commit_request_reaches_the_analysis_runner(client, monkeypatch):
    """§3.1의 extraction_scope·commit_email이 손실 없이 분석 본체까지 전달된다.

    목업이 OWN_COMMIT을 고를 수 있게 된 뒤로는 "화면에서 고른 범위가 실제 분석
    범위가 된다"가 계약이다. 위 테스트들은 결과로 이를 간접 확인하지만, 여기서는
    전달 자체를 직접 고정한다(파이프라인 실행은 대체해 빠르게 유지).
    """
    from app.core import analysis_job

    captured: dict = {}

    def _fake_run(job, **kwargs):
        captured.update(kwargs)
        job.status = "READY"
        job.result = {"applied_scope": kwargs["extraction_scope"], "findings": []}

    monkeypatch.setattr(analysis_job, "run_analysis", _fake_run)

    res = _submit_zip(
        client,
        SAMPLE_REPO,
        {
            "method": "ZIP_WITH_GITLOG",
            "extraction_scope": "OWN_COMMIT",
            "commit_email": "Trainee@Example.com",
            "question_budget": 2,
        },
    )
    assert res.status_code == 202
    _await_job(client, res.json()["job_id"])

    assert captured["extraction_scope"] == "OWN_COMMIT"
    assert captured["commit_email"] == "Trainee@Example.com"
    assert captured["method"] == "ZIP_WITH_GITLOG"
    assert captured["question_budget"] == 2
    assert captured["zip_bytes"], "ZIP 본문이 함께 전달돼야 한다"


def test_job_not_found(client):
    res = client.get(f"{ANALYSES}/does-not-exist")
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"method": "GITHUB_URL"}, "repo_url 누락"),
        ({"method": "ZIP_WITH_GITLOG", "extraction_scope": "OWN_COMMIT"}, "commit_email 누락"),
        ({"method": "NOPE"}, "허용되지 않는 method"),
    ],
)
def test_request_validation(client, payload, reason):
    res = client.post(ANALYSES, json=payload)
    assert res.status_code == 422, reason


def test_zip_method_without_file_is_rejected(client):
    res = client.post(ANALYSES, json={"method": "ZIP_WITH_GITLOG"})
    assert res.status_code == 422


# --- 수집 규칙 (p02-engine.js 이관분) ----------------------------------------


def test_notebook_code_cells_become_virtual_py(tmp_path):
    """D166: .ipynb는 code 셀만 뽑아 가상 .py로 제시하고 markdown은 버린다."""
    nb = json.dumps(
        {
            "cells": [
                {"cell_type": "markdown", "source": ["# 제목\n"]},
                {"cell_type": "code", "source": ["import os\n", "print(os.name)\n"]},
                {"cell_type": "code", "source": "x = 1\n"},
                {"cell_type": "code", "source": ["   \n"]},
            ]
        }
    )
    src = collect.collect_from_zip(_zip_bytes({"nb/analysis.ipynb": nb}), tmp_path)
    assert list(src.files) == ["nb/analysis.ipynb.py"]
    content = src.files["nb/analysis.ipynb.py"]
    assert "import os" in content and "x = 1" in content
    assert "# 제목" not in content
    assert src.notebook_code_count == 1


def test_malformed_notebook_is_counted_as_skipped(tmp_path):
    src = collect.collect_from_zip(_zip_bytes({"bad.ipynb": "{not json"}), tmp_path)
    assert src.files == {}
    assert ".ipynb(코드셀 없음/파싱실패)" in src.skipped_ext_counts


def test_extension_matching_is_case_insensitive(tmp_path):
    """D164: MAIN.PY 같은 이름도 소스로 인식돼야 한다."""
    src = collect.collect_from_zip(_zip_bytes({"MAIN.PY": "x = 1\n"}), tmp_path)
    assert "MAIN.PY" in src.files


def test_zip_slip_members_are_rejected(tmp_path):
    """서버 파일시스템에 해제하므로 경로 탈출 멤버는 버린다(원본에 없던 방어)."""
    src = collect.collect_from_zip(
        _zip_bytes({"../evil.py": "x = 1\n", "ok.py": "y = 2\n"}), tmp_path
    )
    assert "ok.py" in src.files
    assert not any(".." in p for p in src.files)
    assert not (tmp_path.parent / "evil.py").exists()


def test_resolve_connectable_file_matches_by_basename():
    """D179: 파이프라인의 finding.file은 bare basename이다."""
    files = {"src/deep/auth.py": "secret = 1\n"}
    resolved = findings_mod.resolve_connectable_file(files, {"file": "auth.py"})
    assert resolved.path == "src/deep/auth.py"
    assert resolved.via_text is False


def test_resolve_connectable_file_falls_back_to_text_mention():
    """D180: file이 null인 finding은 서술 텍스트에 언급된 파일로 연결한다."""
    files = {"a.py": "1\n", "b.py": "2\n"}
    resolved = findings_mod.resolve_connectable_file(
        files, {"file": None, "finding": "중복 정의: ['a.py', 'b.py']"}
    )
    assert resolved.via_text is True
    assert resolved.all_paths == ["a.py", "b.py"]


# --- .git 이력 기반 귀속 (네트워크 불필요: 로컬 git 저장소를 만들어 검증) --------


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def local_git_repo(tmp_path):
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    try:
        _git(repo, "init", "-q")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git 실행 파일을 사용할 수 없다")
    _git(repo, "config", "user.email", "trainee@example.com")
    _git(repo, "config", "user.name", "교육생")
    (repo / "mine.py").write_text("import os\nX = eval('1')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mine")

    _git(repo, "config", "user.email", "other@example.com")
    _git(repo, "config", "user.name", "다른사람")
    (repo / "theirs.py").write_text("Y = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "theirs")
    return repo


def test_git_history_attribution_is_verified(local_git_repo):
    """B5 GITHUB_URL 경로: `.git` 파싱으로 본인 커밋·파일을 뽑고 VERIFIED로 표기."""
    from app.core import attribution as attribution_mod

    att = attribution_mod.from_git_repo(local_git_repo, "trainee@example.com")
    assert att.commit_count == 1
    assert att.attributed_files == ["mine.py"]
    assert att.verification_status == "VERIFIED"

    # 이메일 대소문자는 무시한다
    upper = attribution_mod.from_git_repo(local_git_repo, "TRAINEE@EXAMPLE.COM")
    assert upper.commit_count == 1

    # 무관한 이메일은 0건 → 호출자가 ATTRIBUTION_REQUIRED로 처리한다
    none_match = attribution_mod.from_git_repo(local_git_repo, "nobody@example.com")
    assert none_match.commit_count == 0


def test_no_git_dir_returns_none(tmp_path):
    """`.git`이 없으면 None → 호출자가 동봉 export로 넘어간다."""
    from app.core import attribution as attribution_mod

    assert attribution_mod.from_git_repo(tmp_path, "trainee@example.com") is None


# --- GitHub clone 경로 (네트워크 의존) ---------------------------------------


@pytest.mark.skip(
    reason="GitHub clone 경로는 네트워크·외부 레포 가용성에 의존해 CI에서 불안정하다. "
    "수동 검증: POST /api/v1/analyses {method: GITHUB_URL, source.repo_url: <공개 레포>}"
)
def test_github_clone_path():
    pass
