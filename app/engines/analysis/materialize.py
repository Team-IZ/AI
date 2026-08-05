""" 요청(GITHUB_URL / ZIP_WITH_GITLOG)을 로컬 임시 디렉터리로 바꾼다.

**출처: 팀원 브랜치 `origin/feature/code-importance-map`의
`app/engines/codemap/materialize.py`** (기준 커밋 `f2db763`). GITHUB_URL 클론 경로와
D12 보안 방어는 그 파일에서 그대로 가져왔다 — 우리가 다시 설계하지 않는다. ZIP 경로만
우리 것(`rules._safe_extract` + `rules._repo_root`)을 쓰도록 바꿨다. 우리 ZIP 경로에는
압축 폭탄·심볼릭 링크 방어와 최상위 폴더 판정이 이미 들어 있어서다.

D12 보안(2026-07-30, 자동 리뷰 발견): `repo_url`·`branch`는 결국 요청 바디를 거쳐
사용자 쪽에서 흘러온 값이라, 검증 없이 git 인자에 넣으면 flag smuggling이 가능하다.
`branch="--upload-pack=touch pwned"`나 `repo_url="ext::sh -c ..."`(ext:: 서브프로토콜은
임의 명령 실행)가 git 자체의 옵션으로 해석된다. 그래서 방어가 넷이다.

  1. repo_url은 http(s) 스킴만            ext::/file:: 등 위험한 서브프로토콜 차단
  2. branch가 "-"로 시작하면 거부          옵션으로 오인될 수 있는 값
  3. 위치 인자 앞에 "--"                  git이 그 뒤를 옵션으로 파싱하지 않게 강제
  4. GIT_ALLOW_PROTOCOL=http:https        코드 검증이 뚫려도 git 레벨에서 2중 방어
     GIT_TERMINAL_PROMPT=0                비공개 레포에서 자격증명 프롬프트에 안 멈춤

셸을 거치지 않는다(`subprocess.run`에 리스트 전달). 클론한 코드는 **읽기만** 한다 —
실행하거나 서브프로세스로 넘기는 일이 없다.

🔴 `--depth 1` 얕은 클론이다. `.git`은 남지만 커밋이 tip 하나뿐이라
`extractionScope="OWN_COMMIT"`(작성자별 필터)은 이걸로 못 한다. 지금은 전체 코드를
출제 대상으로 보기로 해서(2026-08-03) 범위 밖이고, 필요해지면 depth를 푸는 것이
그때의 변경 지점이다. **`.git`을 지우지 않는다** — 지우면 그 시점에 복구가 불가능해진다.

D-git-rce(2026-08-06, `fetch.py` D3): ZIP 안에 `.git`이 들어있으면(`rules._safe_extract`가
이름으로 안 걸러서 그대로 남는다) 그걸 직접 파싱해 git 이력을 뽑는 경로가 있다. 이건
학생이 조작한 `.git/config`(`core.fsmonitor` 등 훅성 설정)를 그대로 실행하면 임의 명령
실행으로 이어지는 **다섯 번째 방어 대상**이다 — "클론한 코드는 읽기만 한다"는 위
원칙이 임베디드 `.git`에도 그대로 적용돼야 한다.

  5. `.git/config`·`hooks/`를 먼저 지운 뒤에만 임베디드 `.git`에 git을 부른다
     `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` + 매 호출
     `-c core.fsmonitor= -c core.hooksPath=/dev/null -c protocol.ext.allow=never`
     `log`/`rev-parse`만 쓰고 `fetch`/`remote`(네트워크) 절대 금지

구현은 `fetch.py`의 `_sandbox_git_env()`/`_strip_git_config()`/`_try_embedded_git_history()`.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

from app.engines.analysis import rules

# 클론 상한. 학생 레포는 작지만 상한이 없으면 job 하나가 워커를 무한정 잡는다.
GIT_CLONE_TIMEOUT_S = 300

_ALLOWED_URL_SCHEMES = {"http", "https"}


# D-fetch-shared (2026-08-06): D12 방어 3종을 public으로 승격 -- app/engines/analysis/
# fetch.py(analysis-inputs 분리 작업)가 그대로 재사용한다.
#   WHY: 클론 인자 검증은 이 파일과 fetch.py 둘 다에서 필요한데, 복사하면 한쪽만
#   패치되고 다른 쪽이 낡는 사고가 난다(이 세션에서 vendor drift로 실제 겪은 것과
#   같은 클래스).
#   COST: 없음 -- 원래도 이 모듈 안에서만 쓰이던 순수 함수라 외부 노출 비용이 없다.
#   EXIT: 세 번째 소비자가 생기면 별도 `security.py` 모듈로 옮기는 것을 검토한다.
def validate_repo_url(repo_url: str) -> None:
    parsed = urlparse(repo_url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ValueError(
            f"repoUrl은 http(s) URL만 허용합니다(ext::/file:: 등 서브프로토콜을 통한 "
            f"명령 실행 방지): {repo_url!r}"
        )


def validate_branch(branch: str) -> None:
    if branch.startswith("-"):
        raise ValueError(f"branch가 '-'로 시작할 수 없습니다(git 옵션으로 오인될 위험): {branch!r}")


def git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "http:https",
    }


def head_sha(repo_dir: str) -> str | None:
    """클론된 디렉터리의 HEAD 커밋. 못 읽으면 None.

    **`commitSha`를 여기서만 채울 수 있다.** ZIP 업로드에는 `.git`이 없을 수도 있어
    요청 값(없다)을 그대로 내보냈는데, 클론 경로에서는 실제 값을 알 수 있다.
    """
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],
            check=True, capture_output=True, timeout=30, env=git_env(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    sha = out.stdout.decode(errors="replace").strip()
    return sha or None


@contextmanager
def materialize(request: Mapping[str, Any], zip_bytes: bytes | None) -> Iterator[str]:
    """method에 따라 클론하거나 ZIP을 풀고 **스캔할 루트 경로**를 내어준다.

    with 블록을 빠져나가면 디렉터리를 지운다 — 코드 원문을 디스크에 남기지 않는다
    (명세 §3.3). 그래서 파일 내용은 블록 안에서 전부 메모리로 읽어야 한다.
    """
    method = request.get("method")
    with tempfile.TemporaryDirectory(prefix="analysis-") as tmp:
        if method == "GITHUB_URL":
            source = request.get("source") or {}
            repo_url = (source.get("repo_url") or "").strip()
            if not repo_url:
                raise ValueError("method=GITHUB_URL인데 source.repoUrl이 없습니다")
            validate_repo_url(repo_url)

            branch = (source.get("branch") or "").strip()
            cmd = ["git", "clone", "--depth", "1"]
            if branch:
                validate_branch(branch)
                cmd += ["--branch", branch]
            cmd += ["--", repo_url, tmp]

            try:
                subprocess.run(cmd, check=True, capture_output=True,
                               timeout=GIT_CLONE_TIMEOUT_S, env=git_env())
            except subprocess.CalledProcessError as exc:
                # stderr에 URL이 그대로 들어 있다. 공개 레포만 받으므로 비밀은 아니지만
                # 원문을 그대로 올리면 job 실패 사유가 장황해진다 — 마지막 줄만 쓴다.
                detail = (exc.stderr or b"").decode(errors="replace").strip().splitlines()
                raise ValueError(
                    f"레포를 가져오지 못했습니다(공개 레포만 지원): "
                    f"{detail[-1] if detail else 'git clone 실패'}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ValueError(
                    f"레포 클론이 {GIT_CLONE_TIMEOUT_S}초를 넘겼습니다"
                ) from exc

            # 클론 결과는 이미 레포 루트다. ZIP처럼 감싸는 폴더가 없다.
            yield tmp
            return

        if zip_bytes is None:
            raise ValueError("method=ZIP_WITH_GITLOG인데 ZIP이 없습니다")
        rules._safe_extract(zip_bytes, Path(tmp))
        yield str(rules._repo_root(Path(tmp)))
