""" app/engines/codemap/materialize.py -- clone/unzip 유일한 지점 테스트

GITHUB_URL 경로의 flag-smuggling/서브프로토콜 방어가 핵심. ZIP_WITH_GITLOG는
D-zip1(2026-08-04)로 폐지됐다 -- 아래 test_zip_with_gitlog_now_rejected가 그
사실만 증명한다.
"""
import os

import pytest

from app.engines.codemap.materialize import default_materialize_repo


# D-zip1 (2026-08-04, app/schemas/analysis.py에 전문): ZIP_WITH_GITLOG 폐지로
# 추출 성공/zip-slip 방어 2종을 검증하던 원래의 4개 테스트는 제거된 기능을
# 테스트하던 것이라 지웠다(materialize.py의 _safe_extractall 자체는 legacy로
# 주석 보존됨, 되살리면 이 테스트들도 git 이력에서 그대로 복원 가능).
# test_zip_with_gitlog_now_rejected 하나만 남겨서 "예전엔 되던 게 이제 막힌다"를
# 증명한다.


def test_zip_with_gitlog_now_rejected():
    request = {"method": "ZIP_WITH_GITLOG"}
    with pytest.raises(ValueError, match="알 수 없는 method"):
        with default_materialize_repo(request, b"anything", None):
            pass


def test_github_url_without_repo_url_raises():
    request = {"method": "GITHUB_URL", "source": {}}
    with pytest.raises(ValueError, match="repo_url"):
        with default_materialize_repo(request, None, None):
            pass


def test_github_url_rejects_non_http_scheme():
    """ ext::/file:: 같은 git 서브프로토콜은 임의 명령 실행으로 이어질 수 있어 차단한다 """
    request = {"method": "GITHUB_URL", "source": {"repo_url": "ext::sh -c 'touch /tmp/pwned'"}}
    with pytest.raises(ValueError, match="http"):
        with default_materialize_repo(request, None, None):
            pass


def test_github_url_rejects_file_scheme():
    request = {"method": "GITHUB_URL", "source": {"repo_url": "file:///etc/passwd"}}
    with pytest.raises(ValueError, match="http"):
        with default_materialize_repo(request, None, None):
            pass


def test_github_url_rejects_branch_starting_with_dash():
    """ branch="--upload-pack=..." 같은 flag-smuggling 시도를 거부한다 """
    request = {
        "method": "GITHUB_URL",
        "source": {"repo_url": "https://github.com/owner/repo", "branch": "--upload-pack=touch pwned"},
    }
    with pytest.raises(ValueError, match="branch"):
        with default_materialize_repo(request, None, None):
            pass


def test_github_url_clone_command_uses_double_dash_separator(monkeypatch):
    """ 검증을 통과한 정상 입력이라도, git이 옵션 파싱을 멈추도록 '--'를 강제로 넣는지 확인 """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    request = {"method": "GITHUB_URL", "source": {"repo_url": "https://github.com/owner/repo", "branch": "main"}}
    with default_materialize_repo(request, None, None):
        pass

    cmd = captured["cmd"]
    assert "--" in cmd
    assert cmd.index("--") < cmd.index("https://github.com/owner/repo")
    assert captured["env"]["GIT_ALLOW_PROTOCOL"] == "http:https"
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_unknown_method_raises():
    request = {"method": "SOMETHING_ELSE"}
    with pytest.raises(ValueError, match="알 수 없는 method"):
        with default_materialize_repo(request, None, None):
            pass


def test_temp_directory_is_cleaned_up_after_context_exits(tmp_path, monkeypatch):
    # D-zip1: ZIP_WITH_GITLOG를 쓰던 원래 버전은 지웠다 -- 이제 유일하게 남은
    # method(GITHUB_URL)로 같은 정리 동작을 검증한다. 실제 clone은 안 타게
    # subprocess.run을 페이크로 바꾼다(test_github_url_clone_command_uses_double_dash_separator
    # 와 같은 패턴).
    monkeypatch.setattr("subprocess.run", lambda cmd, **kwargs: type("R", (), {"returncode": 0})())
    request = {"method": "GITHUB_URL", "source": {"repo_url": "https://github.com/owner/repo"}}
    captured_dir = None
    with default_materialize_repo(request, None, str(tmp_path)) as repo_dir:
        captured_dir = repo_dir
        assert os.path.isdir(captured_dir)
    assert not os.path.isdir(captured_dir)
