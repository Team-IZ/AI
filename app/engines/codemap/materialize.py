""" 요청(GITHUB_URL/ZIP_WITH_GITLOG)을 로컬 임시 디렉터리로 바꾼다 -- 유일한
git/zipfile I/O 지점(불순 edge). 이 함수가 만든 디렉터리 이후로는 read_text만
일어난다(collect.py) -- clone/unzip 자체도 subprocess로 셸을 거치지 않고
`git`/`zipfile` 표준 라이브러리를 직접 호출한다(D12: 임의 명령 실행 없음).
"""
from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

Materializer = Callable[[Mapping[str, Any], "bytes | None", str], "Any"]

GIT_CLONE_TIMEOUT_S = 300


@contextmanager
def default_materialize_repo(
    request: Mapping[str, Any], zip_bytes: bytes | None, workspace_root: str | None
) -> Iterator[str]:
    """ request["method"]에 따라 GitHub 저장소를 얕은 클론하거나 ZIP을 풀어
    임시 디렉터리 경로를 내어준다. with 블록이 끝나면 디렉터리를 지운다. """
    with tempfile.TemporaryDirectory(dir=workspace_root or None, prefix="codemap-") as tmp:
        method = request.get("method")
        if method == "GITHUB_URL":
            source = request.get("source") or {}
            repo_url = source.get("repo_url")
            if not repo_url:
                raise ValueError("method=GITHUB_URL인데 source.repo_url이 없습니다")
            cmd = ["git", "clone", "--depth", "1"]
            branch = source.get("branch")
            if branch:
                cmd += ["--branch", branch]
            cmd += [repo_url, tmp]
            subprocess.run(cmd, check=True, capture_output=True, timeout=GIT_CLONE_TIMEOUT_S)
        elif method == "ZIP_WITH_GITLOG":
            if not zip_bytes:
                raise ValueError("method=ZIP_WITH_GITLOG인데 zip_bytes가 없습니다")
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                _safe_extractall(zf, tmp)  # D12: zip-slip 방어 + 압축 해제만, 실행 없음
        else:
            raise ValueError(f"알 수 없는 method: {method!r}")
        yield tmp


def _safe_extractall(zf: zipfile.ZipFile, dest: str) -> None:
    """ zipfile.ZipFile.extractall()을 그대로 믿지 않는다 -- 학생이 올린 ZIP은 신뢰
    안 되는 입력이라, "../../etc/passwd" 같은 zip-slip 항목이 dest 밖으로 풀리려는
    시도를 하나라도 발견하면 전체를 거부한다(일부만 조용히 건너뛰지 않음 --
    부분적으로 성공한 압축 해제가 더 헷갈리는 상태를 만든다). """
    root = Path(dest).resolve()
    for name in zf.namelist():
        target = (root / name).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"zip-slip 의심 항목 발견, 압축 해제를 거부합니다: {name!r}")
    zf.extractall(dest)
