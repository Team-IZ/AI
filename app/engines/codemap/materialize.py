""" 요청(GITHUB_URL, D-zip1 이전엔 ZIP_WITH_GITLOG도)을 로컬 임시 디렉터리로 바꾼다 --
유일한 git/zipfile I/O 지점(불순 edge). 이 함수가 만든 디렉터리 이후로는 read_text만
일어난다(collect.py) -- clone 자체도 subprocess로 셸을 거치지 않고 `git` 표준
라이브러리를 직접 호출한다(D12: 임의 명령 실행 없음).

D-zip1 (2026-08-04, app/schemas/analysis.py에 전문): ZIP_WITH_GITLOG 폐지로 이
파일의 ZIP 분기와 _safe_extractall()은 주석 처리된 legacy로 남아있다.

D12 보안 강화(자동 리뷰 발견, 2026-07-30): repo_url/branch는 결국 요청 바디를
거쳐 사용자 쪽에서 흘러온 값이라, 검증 없이 그대로 git 인자에 넣으면 flag
smuggling이 가능하다 -- 예를 들어 branch="--upload-pack=touch pwned"나
repo_url="ext::sh -c ...'"(ext:: 서브프로토콜은 임의 명령 실행) 같은 값이
git 자체의 옵션으로 해석될 수 있다. 그래서:
  1. repo_url은 http(s) 스킴만 허용(ext::/file:: 등 위험한 서브프로토콜 차단)
  2. branch가 "-"로 시작하면 거부(옵션으로 오인될 수 있는 값)
  3. 위치 인자 앞에 "--"를 둬서 git이 그 뒤를 옵션으로 파싱하지 않게 강제
  4. GIT_ALLOW_PROTOCOL 환경변수로 http/https만 허용(코드 레벨 검증이 뚫려도
     2중 방어), GIT_TERMINAL_PROMPT=0으로 자격증명 프롬프트에서 멈추지 않게 함
"""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse

Materializer = Callable[[Mapping[str, Any], "bytes | None", str], "Any"]

GIT_CLONE_TIMEOUT_S = 300
_ALLOWED_URL_SCHEMES = {"http", "https"}


def _validate_repo_url(repo_url: str) -> None:
    parsed = urlparse(repo_url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ValueError(
            f"repo_url은 http(s) URL만 허용합니다(ext::/file:: 등 서브프로토콜을 통한 "
            f"명령 실행 방지): {repo_url!r}"
        )


def _validate_branch(branch: str) -> None:
    if branch.startswith("-"):
        raise ValueError(f"branch가 '-'로 시작할 수 없습니다(git 옵션으로 오인될 위험): {branch!r}")


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
            _validate_repo_url(repo_url)

            branch = source.get("branch")
            if branch:
                _validate_branch(branch)

            cmd = ["git", "clone", "--depth", "1"]
            if branch:
                cmd += ["--branch", branch]
            cmd += ["--", repo_url, tmp]  # "--" 이후는 무조건 위치 인자로만 해석(옵션 파싱 차단)

            env = {
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",  # 자격증명 프롬프트에서 멈추지 않음
                "GIT_ALLOW_PROTOCOL": "http:https",  # 2중 방어: ext::/file:: 등을 git 레벨에서도 차단
            }
            subprocess.run(cmd, check=True, capture_output=True, timeout=GIT_CLONE_TIMEOUT_S, env=env)
        # D-zip1: ZIP_WITH_GITLOG 분기, 주석 처리(legacy). 되살리려면
        # app/schemas/analysis.py의 Literal에도 "ZIP_WITH_GITLOG"를 같이 추가할 것.
        # elif method == "ZIP_WITH_GITLOG":
        #     if not zip_bytes:
        #         raise ValueError("method=ZIP_WITH_GITLOG인데 zip_bytes가 없습니다")
        #     with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        #         _safe_extractall(zf, tmp)  # D12: zip-slip 방어 + 압축 해제만, 실행 없음
        else:
            raise ValueError(f"알 수 없는 method: {method!r}")
        yield tmp


# D-zip1: ZIP_WITH_GITLOG의 유일한 호출부였다. 주석 처리, legacy로 보존(zip-slip
# 방어 로직 자체는 여전히 유효하니 ZIP 제출을 다시 받게 되면 그대로 재사용).
# def _safe_extractall(zf: zipfile.ZipFile, dest: str) -> None:
#     """ zipfile.ZipFile.extractall()을 그대로 믿지 않는다 -- 학생이 올린 ZIP은 신뢰
#     안 되는 입력이라, "../../etc/passwd" 같은 zip-slip 항목이 dest 밖으로 풀리려는
#     시도를 하나라도 발견하면 전체를 거부한다(일부만 조용히 건너뛰지 않음 --
#     부분적으로 성공한 압축 해제가 더 헷갈리는 상태를 만든다). """
#     root = Path(dest).resolve()
#     for name in zf.namelist():
#         target = (root / name).resolve()
#         if target != root and root not in target.parents:
#             raise ValueError(f"zip-slip 의심 항목 발견, 압축 해제를 거부합니다: {name!r}")
#     zf.extractall(dest)
