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


# D-fix (redteam audit H9, revisited 2026-08-04): repoUrl은 스킴/netloc 존재만 검사했지
# 호스트가 어디를 가리키는지는 전혀 보지 않았다 -- http://169.254.169.254/... 같은
# 사설·링크로컬 대역으로 이 서버(App Runner) 내부망을 겨냥한 SSRF가 그대로 통과했다.
#   WHY: 호스트를 github.com으로 정확매치 제한한다(계약상 "공개 GitHub repo만 지원"이니
#   기능 손실 없음, AnalysisSource.repo_url 문서화). IP 사전검사(socket.getaddrinfo)도
#   1차로 시도했으나 되돌렸다 -- github.com의 DNS는 GitHub가 소유·운영해 공격자가
#   리바인딩시킬 수 없으므로(GitHub 인프라 자체가 뚫리는 수준이 아닌 한) 호스트 고정만으로
#   이미 SSRF와 DNS rebinding 둘 다 사실상 닫히고, IP 사전검사가 추가로 막는 대상은
#   "github.com 자체가 그 순간 사설 IP로 리바인딩된 경우"뿐이라 한계효용이 낮다. 반면
#   비용은 실재한다 -- test_materialize.py 자신의 원칙("네트워크를 타지 않는다 -- 검증에서
#   걸러지는 값만 넣는다")과 정면으로 부딪혀, 매 검증마다 실제 DNS 조회가 걸리고 테스트가
#   네트워크에 의존하게 됐다. 호스트 고정 하나로 충분한 상황에서 그 비용을 감수할 이유가
#   없었다.
#   COST: github.com 외 호스트(GitHub Enterprise 자체 호스팅, GitLab 등)는 이 계약 밖이라
#   거부된다 -- 오늘 지원 범위에 없던 것이므로 기능 손실 아님.
#   EXIT: 다른 공개 호스트를 지원해야 하면 _ALLOWED_REPO_HOSTS에 추가.
_ALLOWED_REPO_HOSTS = {"github.com"}


# D-fetch-shared (2026-08-06, 수정 2026-08-07 develop 병합 시): scheme 검증(ext::/file::
# 서브프로토콜 차단, D12)만 public으로 유지해 fetch.py가 재사용한다(`_validate_host()`가
# `_validate_scheme()`를 호출) -- **호스트 허용목록은 여기서 분리한다.**
#   WHY: 이 파일(GITHUB_URL 클론 경로)은 host를 github.com 하나로 고정하는 게 맞지만
#   (develop의 H9 SSRF 수정, 위 주석), fetch.py(`/analysis-inputs`)는 애초에 설정
#   가능한 더 넓은 허용목록(`ALLOWED_REPO_HOSTS`, 기본 github.com+www.github.com)을
#   쓰도록 설계돼 있었다 -- 이 함수를 그대로 호출하면 그 설계를 덮어써서 www.github.com이
#   거부되고, 더 심각하게는 fetch.py 자신의 `UNSUPPORTED_HOST` 분류(백엔드 DDL이
#   `INVALID_REPOSITORY_URL`과 별개 값으로 구분해 요구한다)가 이 함수의
#   `INVALID_REPOSITORY_URL`로 뭉개진다(develop 병합 직후 test_unsupported_host_is_rejected
#   회귀로 실제 발견).
#   COST: scheme 검증 로직이 이 함수와 `_validate_scheme` 두 자리에서 호출되지만
#   구현은 한 곳(`_validate_scheme`)뿐이라 중복은 아니다.
#   EXIT: 세 번째 소비자가 생기면 별도 `security.py` 모듈로 옮기는 것을 검토한다.
def _validate_scheme(repo_url: str) -> None:
    parsed = urlparse(repo_url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ValueError(
            f"repoUrl은 http(s) URL만 허용합니다(ext::/file:: 등 서브프로토콜을 통한 "
            f"명령 실행 방지): {repo_url!r}"
        )


def validate_repo_url(repo_url: str) -> None:
    _validate_scheme(repo_url)
    parsed = urlparse(repo_url)
    if parsed.hostname not in _ALLOWED_REPO_HOSTS:
        raise ValueError(
            f"repoUrl은 공개 GitHub repo만 지원합니다(호스트: {', '.join(sorted(_ALLOWED_REPO_HOSTS))}): {repo_url!r}"
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
