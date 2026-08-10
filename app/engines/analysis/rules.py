""" 룰 기반 문제 후보 선별. vendor/ 의 팀원 PoC 규칙부를 감싼다.

여기는 우리 소유고 vendor/ 는 팀원 소유다(vendor/SOURCE.md). 이 파일이 하는 일:
ZIP을 안전하게 풀고 → 원본 진입점 2개를 부르고 → finding을 우리 어휘로 옮긴다.

T6 범위는 "후보까지"다. 문제 3개 확정은 LLM(p04-3)이 이 후보 위에서 한다.
줄 번호·코드 스니펫도 여기서 안 나온다 — finding이 파일 단위이기 때문이고,
그건 T7이 {file, symbol} → locateSymbol 로 산정한다.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

_VENDOR = Path(__file__).parent / "vendor"

# ZIP 폭탄 방어. 학생 제출물 기준으로 넉넉하되 무제한은 아니게 잡는다.
MAX_ENTRIES = 20_000
MAX_TOTAL_BYTES = 500 * 1024 * 1024

# 🔴 업로드(압축본) 상한. 2026-08-10 신설 -- **그전까지 이 상한이 아예 없었다.**
# 위 MAX_TOTAL_BYTES는 압축을 푼 뒤 크기라 `_safe_extract`에서야 검사되는데,
# 그 앞의 `analyses.py:_read_request`가 압축본 전체를 이미 RAM에 올린 뒤였다.
# 2GB를 보내면 어떤 상한에도 닿기 전에 OOM으로 컨테이너가 죽는다(App Runner는
# 인스턴스 1개라 진행 중인 다른 job과 세션까지 같이 날아간다).
#   100MiB 근거: 소스 제출물은 압축 시 보통 1~5MiB다. 정상 제출의 20배 여유이고,
#   백엔드 동시 2건(corePoolSize=2)이 겹쳐도 200MiB다.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# 프롬프트로 옮길 소스 파일 하나의 크기 상한. 이보다 큰 단일 파일은 번들·생성물이라
# 판단 근거가 아니면서 컨텍스트 예산만 먹는다(스캐너의 GENERATED_FILENAME_RE가
# 놓치는 것들이 여기서 걸린다).
MAX_SOURCE_BYTES = 200 * 1024

# D-fix (redteam audit H13, 2026-08-04): vendor/cognition/two_tier_scan.py의
# extract_java_same_package_targets()는 형제 Java 클래스마다 파일 전체 텍스트에
# 정규식 검색을 돌려 O(J²×filesize)다. MAX_SOURCE_BYTES는 스캔이 끝난 뒤(_read_sources)
# 에만 적용되어 스캔 자체(two_tier_scan.scan())는 못 막는다. 상한은 실측이 아니라
# 추정치다(D122 주석의 예시인 "4클래스 학생 과제"보다 수십~수백 배 여유) -- 실제
# 제출물 규모 데이터가 쌓이면 그걸로 보정.
#
# 🔴 2026-08-10 상향(300개/5MiB -> 3000개/100MiB). 위 주석이 예고한 "실제 제출물
# 규모 데이터"가 처음 들어왔다 -- 백엔드 실제 제출물이 5MiB에서 막혔다. 세는 대상이
# 소스만이 아니라 트리의 **모든 파일**이라(.git·빌드 산출물·node_modules 포함)
# 정상 제출물도 쉽게 넘긴다. 업로드 상한(MAX_UPLOAD_BYTES)과 같은 100MiB로 맞춰
# "업로드는 통과했는데 스캔에서 막히는" 구간을 없앴다.
#   COST: 최악의 경우 스캔 비용이 20배. O(J²×filesize)의 J는 소스 파일 수라
#   실제 배수는 이보다 훨씬 작다.
#   EXIT: 세는 대상을 소스 확장자로 좁히면(계획 B안) 이 값을 다시 낮출 수 있다.
MAX_SCAN_FILE_COUNT = 3000
MAX_SCAN_TOTAL_BYTES = 100 * 1024 * 1024

# finding id 접두사 → assessment_problem.problem_type (이슈 #31 D-5의 5종)
# REQUIREMENT_IMPL·EXTERNAL_INTEGRATION은 룰이 만들지 않는다. LLM 선정 단계의 몫이다.
#
# D-tierb-sync (2026-08-05): "tier-b-risk" 접두사 항목을 지웠다. vendor의 D-tierb1
# (2026-07-31, `two_tier_scan.py`)이 이 접두사를 내는 스캔 자체를 폐기해서 영구
# 미도달이었다 -- vendor를 최신으로 동기화하며 여기도 같이 정리한다.
#   WHY: 학생 제출물의 하드코딩 시크릿/eval 결정론적 탐지는 이 서비스에 불필요하다고
#   확인됐다(사용자 확인, 2026-08-05). vendor 원본도 같은 결론 -- 정규식 기반이라
#   matched_text가 짧아 위치 특정이 안 되고 새 패턴을 못 잡아 "위험 목록"으로도
#   "질문 근거"로도 어중간했다(D-tierb1 커밋 메시지).
#   COST: 결정론적 시크릿/eval 탐지 수단을 잃는다. RISK_POINT 문제 유형은 이제 이
#   경로로는 영구히 안 나온다(schemas의 유효값 자체는 유지 -- 다른 경로가 낼 수도
#   있어 스키마는 안 좁힌다).
#   EXIT: 이 능력이 다시 필요해지면 정규식 재도입이 아니라 semgrep 같은 전용 SAST를
#   붙인다(vendor 원본 커밋이 명시한 방향). 그때 이 dict에 접두사를 다시 추가한다.
_PROBLEM_TYPE_BY_PREFIX = {
    "architecture-diffusion": "DESIGN_CHOICE",
    "cognition-isolation": "COMPLEXITY_HOTSPOT",
    "repeated-pattern": "COMPLEXITY_HOTSPOT",
}
_DEFAULT_PROBLEM_TYPE = "COMPLEXITY_HOTSPOT"


def _load_vendor():
    """vendor 하위 3개 디렉터리를 sys.path에 꽂고 진입점 2개를 돌려준다.

    vendor 파일들이 서로를 플랫 이름으로 import한다(`from subrubric import ...`).
    원본을 안 고치기로 했으므로 경로를 맞추는 쪽이 우리다 — SOURCE.md 참조.
    스레드 여러 개가 동시에 들어올 수 있지만, 최악이 sys.path 중복 항목이라 무해하다.
    """
    for sub in ("cognition", "judgment", "feedback"):
        path = str((_VENDOR / sub).resolve())
        if path not in sys.path:
            sys.path.insert(0, path)

    # 편집기가 못 찾는 게 정상이다 — 위에서 sys.path를 바꾼 뒤에야 보이는 모듈이다.
    import score_findings
    import two_tier_scan

    return two_tier_scan, score_findings


@lru_cache(maxsize=1)
def extractor_version() -> int:
    """이 결과를 만든 룰의 버전. vendor의 .py·.json 전부를 해시한다.

    rank_weights.json 같은 데이터 파일이 결과를 바꾸므로 코드만 해싱하면
    "같은 버전인데 결과가 다르다"가 생긴다. Problem.extractorVersion에 실린다.

    **정수로 돌려준다** — DB `assessment_problem.extractor_version`이
    `INTEGER CHECK (> 0)`이다. 문자열(`"rules-a1b2…"`)을 보내면 Spring INSERT가
    깨지므로, 해시를 PostgreSQL INTEGER 범위 안으로 접어 넣는다.

    ponytail: 해시를 접으므로 값이 사람에게 안 읽히고 순서도 없다(버전이 오르지
    않는다). "같은 룰이면 같은 값, 다른 룰이면 다른 값"만 보장하면 되는 자리라
    충분하다. 사람이 읽는 버전이 필요해지면 별도 필드를 요청한다.
    """
    digest = hashlib.sha256()
    for path in sorted(_VENDOR.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".json"):
            continue
        digest.update(path.relative_to(_VENDOR).as_posix().encode())
        digest.update(path.read_bytes())
    # 2^31-1을 넘으면 INTEGER에 안 들어간다. 0도 CHECK에 걸리므로 1부터 시작한다.
    return int(digest.hexdigest()[:12], 16) % 2_147_483_647 + 1


def _safe_extract(zip_bytes: bytes, dest: Path) -> None:
    """ZIP을 dest 안에만 푼다.

    학생이 올린 파일이라 항목 이름을 신뢰할 수 없다. `../../` 가 섞여 있으면
    서버 파일을 덮어쓴다(Zip Slip). 심볼릭 링크는 바깥을 가리킬 수 있어 버리고,
    압축 해제 크기도 막는다(ZIP 폭탄 — 몇 KB가 수 GB로 부푼다).
    """
    dest = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError(f"ZIP 항목이 너무 많습니다: {len(infos)}")
        total = sum(i.file_size for i in infos)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"압축 해제 크기가 한도를 넘습니다: {total} bytes")

        for info in infos:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:  # symlink
                continue
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest):
                raise ValueError(f"ZIP 경로가 대상 디렉터리를 벗어납니다: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


# 벗기면 안 되는 최상위 폴더 이름. 이 이름들은 **프로젝트의 일부**라서 경로에 남아야
# 한다. 나머지 이름(`repo-main`·`library-app` 등)은 감싸는 껍데기로 보고 벗긴다 —
# 모르는 이름은 벗기는 쪽이 기존 동작이고, 실패해도 옛날과 같은 결과다.
_SOURCE_DIR_NAMES = {
    "src", "app", "lib", "source", "sources", "main", "java", "com", "org",
    "test", "tests", "docs", "static", "public", "assets", "include", "script", "scripts",
}


def _repo_root(extracted: Path) -> Path:
    """GitHub ZIP처럼 최상위 폴더 하나로 감싸여 있으면 그 안으로 내려간다.

    안 내려가면 스캔이 파일을 하나도 못 찾는 게 아니라 경로가 `repo-main/app/x.py`로
    나와, 나중에 학생이 보는 코드 위치와 어긋난다.

    🔴 **폴더가 하나라는 것만으로 내려가면 안 된다** (2026-08-03 실측). 학생이
    프로젝트 폴더 안에서 압축하면 최상위가 `src/` 하나뿐인 ZIP이 나오고, 그러면
    `src/`를 래퍼로 착각해 벗겨서 `src/main/java/App.java`가 `main/java/App.java`로
    응답된다. 백엔드는 그 경로로 파일을 못 찾는데 에러도 안 난다.

    그래서 **폴더 이름이 소스 폴더 이름이면 벗기지 않는다.** `repo-main`이나
    `library-app`은 껍데기고 `src`는 프로젝트의 일부다.
    """
    entries = list(extracted.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        if entries[0].name.lower() not in _SOURCE_DIR_NAMES:
            return entries[0]
    return extracted


def _to_candidate(finding: dict[str, Any]) -> dict[str, Any]:
    """PoC finding 하나를 우리 후보 어휘로 옮긴다.

    sourcePath가 None인 후보가 있다 — `repeated-pattern:duplicate-definition`은
    같은 정의가 여러 파일에 흩어진 것이라 단일 경로가 없다. Problem.sourcePath는
    필수 문자열이라 이대로는 문제가 못 되지만 **여기서 버리지 않는다.**
    어느 파일을 대표로 삼을지는 LLM 선정 단계가 판단할 수 있고, 지금 버리면
    그 판단 기회 자체가 사라진다.
    """
    finding_id = finding.get("id", "")
    prefix = finding_id.split(":", 1)[0]
    return {
        "finding_id": finding_id,
        "source_path": finding.get("file"),
        "problem_type": _PROBLEM_TYPE_BY_PREFIX.get(prefix, _DEFAULT_PROBLEM_TYPE),
        "priority": float(finding.get("rank_score") or 0.0),
        "rank": finding.get("rank"),
        "summary": finding.get("finding", ""),
        "lang": finding.get("lang"),
        # "왜 이 후보인가"의 근거 전체. Problem 스키마엔 담을 자리가 아직 없다(T7 결정 사항).
        "selection_evidence": {
            "subrubric": finding.get("subrubric", {}),
            "rank_evidence": finding.get("rank_evidence", {}),
        },
    }


def _read_sources(two_tier_scan, root: str) -> dict[str, str]:
    """스캔 대상 소스를 {상대경로: 본문}으로 읽는다.

    **스캐너와 같은 목록을 쓴다**(`find_src_files`). 우리가 확장자 목록을 따로 들면
    "룰이 본 파일"과 "LLM이 본 파일"이 갈리고, 그러면 finding이 가리키는 파일이
    프롬프트에 없는 상황이 생긴다 — 모델은 그걸 "없는 파일"로 취급한다.

    임시 디렉터리는 곧 지워지므로 **여기서 메모리로 옮기지 않으면 못 읽는다.**
    디스크에 남기지 않는다는 원칙(명세 §3.3)은 그대로다.
    """
    files: dict[str, str] = {}
    root_path = Path(root)
    for path in two_tier_scan.find_src_files(root):
        p = Path(path)
        try:
            if p.stat().st_size > MAX_SOURCE_BYTES:
                continue      # 번들·생성물. 프롬프트 예산만 먹고 판단 근거가 안 된다
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files[p.relative_to(root_path).as_posix()] = text
    return files


def find_candidates(zip_bytes: bytes) -> dict[str, Any]:
    """ZIP → 룰 기반 문제 후보 목록 + 소스 본문. rank 내림차순으로 이미 정렬돼 있다.

    임시 디렉터리는 빠져나갈 때 지워진다 — 코드 원문을 디스크에 남기지 않는다(명세 §3.3).
    소스 본문은 **메모리로만** 들고 나온다. 다운스트림(p04-1·p04-2·질문·줄 번호 산정)이
    전부 파일 내용을 봐야 하는데, 여기서 안 읽으면 ZIP을 두 번 풀어야 한다.

    CPU 작업이라 이벤트 루프에서 직접 부르면 안 된다. 지금은 run_analysis가
    동기 함수(`def`)라 Starlette이 threadpool로 돌려준다. async def로 바꾸지 말 것.
    """
    with tempfile.TemporaryDirectory(prefix="analysis-") as tmp:
        _safe_extract(zip_bytes, Path(tmp))
        return scan_directory(str(_repo_root(Path(tmp))))


def _strip_symlinks(root: str) -> None:
    """스캔 전 심볼릭 링크를 전부 지운다(2026-08-04 redteam 감사 발견).

    ZIP 경로(`_safe_extract`)는 심볼릭 링크 항목을 애초에 안 풀어서 안전하지만,
    GITHUB_URL 클론 경로(`materialize.py`)는 git이 만든 심볼릭 링크가 그대로 남는다.
    vendor/ 스캐너가 내부적으로 링크를 따라가는지 우리가 보장할 수 없으므로(팀원
    소유 코드, vendor/SOURCE.md), 두 경로가 같은 결과를 내려면(위 docstring) vendor를
    부르기 전에 여기서 링크 자체를 제거하는 것이 유일하게 양쪽을 동시에 막는 지점이다.

    top-down + `followlinks=False`로 걷고 `dirnames`를 그 자리에서 가지치기한다 —
    중간 경로가 링크인 채로 `os.path.join`을 하면 `unlink`가 링크를 타고 나가버려
    루트 밖의 실제 파일을 지울 위험이 있다. 발견 즉시 가지치기하면 그 경로 자체가
    생기지 않는다.
    """
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        keep = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                os.unlink(full)
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                os.unlink(full)


class ScanLimitExceeded(ValueError):
    """스캔 입력 규모 상한 초과. `jobs.py`가 이걸 보고 `FILE_TOO_LARGE`로 옮긴다.

    🔴 2026-08-10: 예전엔 맨 ValueError였다. fetch 단계가 아니라 **엔진 스캔 단계**에서
    나므로 `fetch.py:_classify_zip_error`가 못 잡고, `jobs.py`의 catch-all까지 새어나가
    `MODEL_ERROR`로 기록됐다 -- LLM을 한 번도 안 부른 실패에 "모델 실패"가 찍힌다.
    ValueError를 상속해 기존에 ValueError를 잡던 자리는 그대로 동작한다.
    """


def _enforce_scan_limits(root: str) -> None:
    """vendor 스캐너(우리 소유 아님)를 부르기 전에 입력 규모를 캡핑한다(H13).

    파일 개수만 캡하면 "적은 개수의 거대한 파일"에는 여전히 뚫리므로 합산 바이트도
    같이 본다. 스캐너가 실제로 읽는 파일(소스 확장자)만이 아니라 전체 파일을 센다 —
    find_src_files() 자신의 os.walk 비용과, 향후 vendor가 다른 파일도 처리하게 될
    가능성까지 같이 막아 두는 쪽이 더 안전하다.
    """
    file_count = 0
    total_bytes = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            file_count += 1
            if file_count > MAX_SCAN_FILE_COUNT:
                raise ScanLimitExceeded(f"제출물 파일 수가 상한({MAX_SCAN_FILE_COUNT}개)을 넘습니다")
            try:
                total_bytes += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            if total_bytes > MAX_SCAN_TOTAL_BYTES:
                raise ScanLimitExceeded(
                    f"제출물 총 용량이 상한({MAX_SCAN_TOTAL_BYTES} bytes)을 넘습니다"
                )


def scan_directory(root: str) -> dict[str, Any]:
    """이미 풀려 있는(또는 클론된) 디렉터리를 스캔한다.

    `find_candidates`에서 갈라져 나왔다 — GITHUB_URL은 ZIP 바이트가 없고
    `materialize.py`가 클론한 디렉터리를 준다. 두 경로가 같은 스캔을 타야
    "링크로 낸 제출물"과 "ZIP으로 낸 제출물"의 결과가 갈리지 않는다.
    """
    _enforce_scan_limits(root)
    _strip_symlinks(root)
    two_tier_scan, score_findings = _load_vendor()

    scan = two_tier_scan.scan(root)
    judged = score_findings.score(scan, root)
    files = _read_sources(two_tier_scan, root)

    return {
        "extractor_version": extractor_version(),
        "hub": judged.get("hub"),
        "file_count": scan.get("total_source_files", 0),
        "language_notice": scan.get("language_coverage_notice"),
        "candidates": [_to_candidate(f) for f in judged.get("findings", [])],
        "files": files,
    }