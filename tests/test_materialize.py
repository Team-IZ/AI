""" GITHUB_URL 클론 경로의 입력 검증.

**네트워크를 타지 않는다** — 검증에서 걸러지는 값만 넣는다. git이 실제로 불리면
그 자체가 회귀다(검증을 통과했다는 뜻이라서).
"""
import pytest

from app.engines.analysis import materialize


def _materialize(source, method="GITHUB_URL", zip_bytes=None):
    with materialize.materialize({"method": method, "source": source}, zip_bytes):
        pass


@pytest.mark.parametrize("repo_url", [
    "ext::sh -c 'touch pwned'",     # ext:: 서브프로토콜은 임의 명령 실행이다
    "file:///etc/passwd",           # 로컬 경로 접근
    "git://github.com/o/r.git",     # 평문 프로토콜
    "not-a-url",
])
def test_dangerous_repo_url_is_rejected(repo_url):
    with pytest.raises(ValueError, match="http"):
        _materialize({"repo_url": repo_url})


def test_branch_that_looks_like_an_option_is_rejected():
    """`--upload-pack=...`이 git 옵션으로 해석되면 임의 명령이 돈다."""
    with pytest.raises(ValueError, match="branch"):
        _materialize({
            "repo_url": "https://github.com/owner/repo",
            "branch": "--upload-pack=touch pwned",
        })


def test_missing_repo_url_is_rejected():
    with pytest.raises(ValueError, match="repoUrl"):
        _materialize({})


def test_zip_path_yields_repo_root():
    """ZIP 경로는 우리 압축 해제(zip-slip·폭탄 방어 + 최상위 폴더 판정)를 그대로 쓴다."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo-main/a.py", "x = 1\n")

    with materialize.materialize({"method": "ZIP_WITH_GITLOG"}, buf.getvalue()) as root:
        assert (__import__("pathlib").Path(root) / "a.py").exists()


def test_zip_method_without_bytes_is_rejected():
    with pytest.raises(ValueError, match="ZIP"):
        _materialize({}, method="ZIP_WITH_GITLOG")
