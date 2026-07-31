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

# finding id 접두사 → assessment_problem.problem_type (이슈 #31 D-5의 5종)
# REQUIREMENT_IMPL·EXTERNAL_INTEGRATION은 룰이 만들지 않는다. LLM 선정 단계의 몫이다.
_PROBLEM_TYPE_BY_PREFIX = {
    "architecture-diffusion": "DESIGN_CHOICE",
    "tier-b-risk": "RISK_POINT",
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
def extractor_version() -> str:
    """이 결과를 만든 룰의 버전. vendor의 .py·.json 전부를 해시한다.

    rank_weights.json 같은 데이터 파일이 결과를 바꾸므로 코드만 해싱하면
    "같은 버전인데 결과가 다르다"가 생긴다. Problem.extractorVersion에 실린다.
    """
    digest = hashlib.sha256()
    for path in sorted(_VENDOR.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".json"):
            continue
        digest.update(path.relative_to(_VENDOR).as_posix().encode())
        digest.update(path.read_bytes())
    return f"rules-{digest.hexdigest()[:12]}"


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


def _repo_root(extracted: Path) -> Path:
    """GitHub ZIP처럼 최상위 폴더 하나로 감싸여 있으면 그 안으로 내려간다.

    안 내려가면 스캔이 파일을 하나도 못 찾는 게 아니라 경로가 `repo-main/app/x.py`로
    나와, 나중에 학생이 보는 코드 위치와 어긋난다.
    """
    entries = list(extracted.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
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


def find_candidates(zip_bytes: bytes) -> dict[str, Any]:
    """ZIP → 룰 기반 문제 후보 목록. rank 내림차순으로 이미 정렬돼 있다.

    임시 디렉터리는 빠져나갈 때 지워진다 — 코드 원문을 남기지 않는다(명세 §3.3).
    CPU 작업이라 이벤트 루프에서 직접 부르면 안 된다. 지금은 run_analysis가
    동기 함수(`def`)라 Starlette이 threadpool로 돌려준다. async def로 바꾸지 말 것.
    """
    two_tier_scan, score_findings = _load_vendor()

    with tempfile.TemporaryDirectory(prefix="analysis-") as tmp:
        _safe_extract(zip_bytes, Path(tmp))
        root = str(_repo_root(Path(tmp)))
        scan = two_tier_scan.scan(root)
        judged = score_findings.score(scan, root)

    return {
        "extractor_version": extractor_version(),
        "hub": judged.get("hub"),
        "file_count": scan.get("total_source_files", 0),
        "language_notice": scan.get("language_coverage_notice"),
        "candidates": [_to_candidate(f) for f in judged.get("findings", [])],
    }