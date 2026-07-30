""" app/engines/codemap/materialize.py -- clone/unzip 유일한 지점 테스트

zip-slip 방어가 핵심: 학생이 올린 ZIP은 신뢰 안 되는 입력이라, 실행은 물론이고
"압축을 어디에 풀지"조차 그대로 믿으면 안 된다.
"""
import io
import os
import zipfile

import pytest

from app.engines.codemap.materialize import default_materialize_repo


def _make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_zip_with_gitlog_extracts_files(tmp_path):
    zip_bytes = _make_zip({"main.py": "print(1)\n", "src/util.py": "x = 1\n"})
    request = {"method": "ZIP_WITH_GITLOG"}
    with default_materialize_repo(request, zip_bytes, str(tmp_path)) as repo_dir:
        assert (tmp_path.__class__(repo_dir) / "main.py").read_text() == "print(1)\n"
        assert (tmp_path.__class__(repo_dir) / "src" / "util.py").read_text() == "x = 1\n"


def test_zip_without_bytes_raises():
    request = {"method": "ZIP_WITH_GITLOG"}
    with pytest.raises(ValueError, match="zip_bytes"):
        with default_materialize_repo(request, None, None):
            pass


def test_zip_slip_path_traversal_is_rejected(tmp_path):
    """ 학생 ZIP이 ../../로 저장소 밖을 가리키면 압축 해제 자체를 거부한다 (D12) """
    zip_bytes = _make_zip({"../../../etc/evil.py": "malicious\n"})
    request = {"method": "ZIP_WITH_GITLOG"}
    with pytest.raises(ValueError, match="zip-slip"):
        with default_materialize_repo(request, zip_bytes, str(tmp_path)):
            pass


def test_zip_slip_absolute_path_is_rejected(tmp_path):
    zip_bytes = _make_zip({"/etc/evil.py": "malicious\n"})
    request = {"method": "ZIP_WITH_GITLOG"}
    with pytest.raises(ValueError, match="zip-slip"):
        with default_materialize_repo(request, zip_bytes, str(tmp_path)):
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


def test_temp_directory_is_cleaned_up_after_context_exits(tmp_path):
    zip_bytes = _make_zip({"a.py": "x\n"})
    request = {"method": "ZIP_WITH_GITLOG"}
    captured_dir = None
    with default_materialize_repo(request, zip_bytes, str(tmp_path)) as repo_dir:
        captured_dir = repo_dir
        assert os.path.isdir(captured_dir)
    assert not os.path.isdir(captured_dir)
