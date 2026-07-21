"""파이프라인 finding → 명세 §3.2 `result.findings[]` 변환 + `code_context` 발췌.

파이프라인 원본은 **줄 번호를 내지 않는다** (`score_findings.score()`의 finding은
`id/file/finding/priority/subrubric/rank_*`뿐이고 `file`은 bare basename이다).
반면 명세 §3.2는 `line_start`/`line_end`와 `code_context`를 요구한다 — 그 간극을
여기서 메운다. 파이프라인은 건드리지 않는다 (PLAN §4).

파일 연결 규칙은 `shared/p02-engine.js`에서 이관했다:
- `findFileByBasename` (D179) → `_find_file_by_basename`
- `findReferencedFiles`  (D180) → `_find_referenced_files`
- `resolveConnectableFile`      → `resolve_connectable_file`
- `MAX_CONNECT_FILES = 3`       → 동일 상수
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# p02-engine.js 76행과 동일 (D181)
MAX_CONNECT_FILES = 3

# code_context 발췌 크기.
# 문자 상한은 목업이 실제로 쓰던 값을 그대로 계승한다 —
# prompt_manifest.json의 p03-1 `truncation.code_context = 4000`.
CODE_CONTEXT_CHAR_CAP = 4000
# 매치 지점 위아래로 잡는 줄 수. 명세 예시(finding 10~24줄 / context 1~60줄)의
# "context가 finding보다 넓다"는 의도에 맞춘 잠정값 — 확정 수치는 명세에 없다.
CONTEXT_LINES_BEFORE = 20
CONTEXT_LINES_AFTER = 20

_MATCHED_TEXT = re.compile(r"matched_text='(.*)'")


@dataclass
class _Resolved:
    path: str
    via_text: bool
    all_paths: list[str]


def _find_file_by_basename(files: dict[str, str], basename: str | None) -> str | None:
    """원본 `findFileByBasename` (D179).

    파이프라인의 `finding.file`은 항상 bare basename인데(two_tier_scan이
    `os.path.basename()`으로 키를 만든다) `files`는 전체 상대경로 키다.
    """
    if not basename:
        return None
    if basename in files:
        return basename
    for rel_path in files:
        if rel_path.split("/")[-1] == basename:
            return rel_path
    return None


def _find_referenced_files(files: dict[str, str], finding_text: str | None) -> list[str]:
    """원본 `findReferencedFiles` (D180).

    `repeated-pattern:duplicate-definition`처럼 `file: null`인 finding은 실제 파일명이
    자유 서술 텍스트 안에만 등장한다. repr 파싱 대신 basename 부분 문자열로 찾는다.
    """
    if not finding_text:
        return []
    return [p for p in files if p.split("/")[-1] in finding_text]


def resolve_connectable_file(files: dict[str, str], finding: dict) -> _Resolved | None:
    """원본 `resolveConnectableFile`: 직접 매치(D179) 우선, 텍스트 언급(D180) 폴백."""
    direct = _find_file_by_basename(files, finding.get("file"))
    if direct:
        return _Resolved(path=direct, via_text=False, all_paths=[direct])
    mentioned = _find_referenced_files(files, finding.get("finding"))
    if mentioned:
        return _Resolved(path=mentioned[0], via_text=True, all_paths=mentioned)
    return None


def _locate_match(content: str, finding_text: str | None) -> tuple[int, int] | None:
    """finding 서술의 `matched_text='...'`가 있는 줄 범위를 찾는다 (1-based).

    Tier-B 위험 finding은 서술에 매치 원문을 담고 있어 이 방법으로 정확히 짚힌다.
    구조(Tier-A) finding처럼 매치 원문이 없으면 None → 파일 전체를 컨텍스트로 쓴다.
    """
    m = _MATCHED_TEXT.search(finding_text or "")
    if not m:
        return None
    needle = m.group(1).strip()
    if not needle:
        return None

    lines = content.splitlines()
    # 매치 원문이 여러 줄일 수 있으므로 첫 줄 기준으로 찾고 줄 수만큼 확장한다.
    needle_lines = needle.splitlines() or [needle]
    head = needle_lines[0]
    for idx, line in enumerate(lines):
        if head in line:
            return idx + 1, idx + len(needle_lines)
    return None


def build_code_context(path: str, content: str, finding_text: str | None) -> dict:
    """§3.3 파편 저장 원칙: Spring이 영속화할 코드 발췌를 만든다.

    반환: `{path, line_start, line_end, snippet}` (+ finding 자체의 좁은 줄 범위는
    호출자가 `_locate_match` 결과로 별도 사용).
    """
    lines = content.splitlines()
    total = len(lines)
    located = _locate_match(content, finding_text)

    if located is None:
        start, end = 1, total
    else:
        start = max(1, located[0] - CONTEXT_LINES_BEFORE)
        end = min(total, located[1] + CONTEXT_LINES_AFTER)

    snippet = "\n".join(lines[start - 1 : end])
    if len(snippet) > CODE_CONTEXT_CHAR_CAP:
        snippet = snippet[:CODE_CONTEXT_CHAR_CAP]
        # 잘린 만큼 line_end를 실제 내용에 맞춰 되돌린다 — 저장될 값이
        # snippet과 어긋나면 복원·이의제기 때 엉뚱한 범위를 가리키게 된다.
        end = start + snippet.count("\n")

    return {
        "path": path,
        "line_start": start,
        "line_end": end,
        "snippet": snippet,
    }


def to_api_findings(raw_findings: list[dict], files: dict[str, str]) -> list[dict]:
    """파이프라인 finding 목록을 명세 §3.2 형태로 변환한다."""
    out: list[dict] = []
    for raw in raw_findings:
        resolved = resolve_connectable_file(files, raw)
        finding_text = raw.get("finding")

        code_context = None
        line_start = line_end = None
        source_path = None

        if resolved is not None:
            source_path = resolved.path
            content = files.get(resolved.path, "")
            located = _locate_match(content, finding_text)
            if located is not None:
                line_start, line_end = located
            code_context = build_code_context(resolved.path, content, finding_text)

        out.append(
            {
                "finding_id": raw.get("id"),
                # 명세 §3.2가 제시한 값은 "CODE" 하나뿐이다. 파이프라인에는
                # 이에 대응하는 분류 필드가 없어 상수로 채운다.
                "type": "CODE",
                "priority": raw.get("priority"),
                "source_path": source_path,
                "line_start": line_start,
                "line_end": line_end,
                "summary": finding_text,
                "evidence_hash": _evidence_hash(raw.get("id"), code_context),
                "code_context": code_context,
            }
        )
    return out


def _evidence_hash(finding_id: str | None, code_context: dict | None) -> str | None:
    """근거의 지문.

    명세 §3.2에 `evidence_hash` 필드는 있으나 **산출 방식이 정의돼 있지 않다**.
    여기서는 "finding_id + 경로 + 줄 범위 + 발췌 원문"의 sha256으로 정했다 —
    같은 커밋을 재분석하면 같은 값이 나오고, 저장된 발췌가 나중에 변조되면 달라진다.

    finding_id를 함께 넣는 이유: 작은 파일에서는 서로 다른 finding 둘이 같은
    발췌 창을 공유하는 일이 흔하다(실측됨). 발췌만 해싱하면 두 finding의 해시가
    충돌해 finding별 식별자로 쓸 수 없다. 백엔드와 합의되면 이 함수만 바꾸면 된다.
    """
    if not code_context:
        return None
    payload = "{id}\n{path}:{line_start}-{line_end}\n{snippet}".format(
        id=finding_id or "", **code_context
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
