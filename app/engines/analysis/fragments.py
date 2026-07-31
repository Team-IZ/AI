""" {file, symbol} 참조를 실제 소스와 대조해 코드 파편·줄 번호를 산정한다.
code-fragment.js 포팅(JS→Python이라 이식이 아니라 다시 쓴 것).

**LLM에게 줄 번호를 세게 하지 않는다.** LLM은 코드를 그대로 인용하는 건 잘하지만
세는 건 못한다. 그래서 실제 코드 한 줄(symbol)만 옮겨 적게 하고 그게 몇 번째 줄인지는
여기서 찾는다. "산정된 사실"과 "LLM의 주장"을 분리하는 장치다.

못 찾으면 valid=False로 두고 줄 번호를 비운다 — 지어낸 위치를 근거로 보여주면
"코드 파편이 곧 근거"라는 전제가 무너진다.
"""

import re
from typing import Any

CONTEXT_LINES = 2      # 지목 범위 위아래로 더 보여줄 줄 수
BLOCK_MAX_LINES = 40   # 블록 끝 추정 최대 범위

_WS = re.compile(r"\s+")
_INDENT = re.compile(r"^\s*")
_OPEN = "([{"
_CLOSE = ")]}"

def _bracket_delta(line: str) -> int:
    """그 줄이 여닫은 괄호의 순증감.

    문자열·주석 안의 괄호도 센다 — 정확한 파서가 아니다. 블록 끝을 몇 줄
    더 볼지 정하는 데만 쓰이고, 시작 줄은 문자열 매칭으로 이미 확정돼 있다.
    """
    return sum(1 for c in line if c in _OPEN) - sum(1 for c in line if c in _CLOSE)

def split_lines(content: str) -> list[str]:
    return re.split(r"\r\n|\r|\n", str(content))


def _normalize(s: str) -> str:
    return _WS.sub(" ", s).strip()


def resolve_file(files: dict[str, str], ref_file: str | None) -> str | None:
    """정확한 경로 우선, 없으면 파일명(basename)으로 폴백.

    LLM이 `app/main.py`를 `main.py`로 줄여 인용하는 일이 잦다. 동명 파일이 여러 개면
    첫 번째를 쓴다 — 틀릴 수 있지만 symbol 매칭이 한 번 더 걸러낸다.
    """
    if not ref_file:
        return None
    if ref_file in files:
        return ref_file
    base = ref_file.split("/")[-1]
    for path in files:
        if path.split("/")[-1] == base:
            return path
    return None


def _symbol_candidates(symbol: str) -> list[str]:
    """symbol에서 매칭에 쓸 문자열들을 뽑는다(앞에 올수록 우선).

    매니페스트는 "한 줄"을 요구하지만 실측에서 모델이 함수 시그니처 전체를 여러 줄로
    붙여 왔다(p04-1, 2026-07-31). 줄 단위로만 매칭하면 그런 항목은 전부 버려진다.
    첫 줄만으로 한 번 더 시도해 살린다 — 시작 줄만 맞으면 블록 추정이 나머지를 채운다.
    """
    raw = str(symbol or "").strip()
    if not raw:
        return []
    candidates = [raw]
    if "\n" in raw:
        first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
        if first:
            candidates.append(first)
    return candidates


def locate_symbol(files: dict[str, str], ref_file: str | None, symbol: str) -> dict[str, Any]:
    """symbol이 파일의 몇 번째 줄에 있는지 찾는다.

    반환: {valid: True, file, line_start, line_end, matched_line}
          {valid: False, reason}
    """
    resolved = resolve_file(files, ref_file)
    if resolved is None:
        return {"valid": False, "reason": f"파일을 찾을 수 없음: {ref_file}"}

    candidates = _symbol_candidates(symbol)
    if not candidates:
        return {"valid": False, "reason": "symbol이 비어있음"}

    lines = split_lines(files[resolved])
    idx = -1
    for needle in candidates:
        idx = next((i for i, ln in enumerate(lines) if needle in ln), -1)
        if idx == -1:
            # 들여쓰기·연속 공백이 뭉개진 인용을 살린다.
            norm = _normalize(needle)
            idx = next((i for i, ln in enumerate(lines) if norm in _normalize(ln)), -1)
        if idx != -1:
            break

    if idx == -1:
        return {"valid": False, "reason": f'코드에서 찾을 수 없음: "{candidates[0][:60]}"'}

    # 블록 끝 추정 2단계.
    #  ① 아직 안 닫힌 괄호가 있으면 계속 — 여러 줄 시그니처는 닫는 줄의 들여쓰기가
    #     시작줄과 같아서, 들여쓰기만 보면 시그니처 한복판에서 끊긴다(실측 재현).
    #  ② 닫힌 뒤에는 들여쓰기가 시작줄 이하로 얕아지는 줄 전까지.
    # 시작 줄은 문자열 매칭으로 확정된 사실이라 항상 정확하다 — 이 추정은
    # "얼마나 더 보여줄지"에만 영향을 준다.
    base_indent = len(_INDENT.match(lines[idx]).group(0))
    depth = _bracket_delta(lines[idx])
    end_idx = idx
    for i in range(idx + 1, min(len(lines), idx + BLOCK_MAX_LINES)):
        line = lines[i]
        if depth > 0:
            end_idx = i
            depth += _bracket_delta(line)
            continue
        if not line.strip():
            end_idx = i
            continue
        if len(_INDENT.match(line).group(0)) <= base_indent:
            break
        end_idx = i
    while end_idx > idx and not lines[end_idx].strip():
        end_idx -= 1

    return {"valid": True, "file": resolved,
            "line_start": idx + 1, "line_end": end_idx + 1, "matched_line": idx + 1}


def extract_fragment(files: dict[str, str], ref_file: str, symbol: str) -> dict[str, Any]:
    """symbol이 가리키는 코드 파편을 앞뒤 문맥 2줄과 함께 뽑는다.

    text는 evidenceHash의 대상이 아니다 — 해시는 codeSnippet(문맥 없는 원문) 기준이다.
    """
    located = locate_symbol(files, ref_file, symbol)
    if not located["valid"]:
        return located

    lines = split_lines(files[located["file"]])
    start, end = located["line_start"], located["line_end"]
    ctx_start = max(1, start - CONTEXT_LINES)
    ctx_end = min(len(lines), end + CONTEXT_LINES)
    return {
        **located,
        "snippet": "\n".join(lines[start - 1:end]),
        "context_start": ctx_start,
        "context_end": ctx_end,
        "context_text": "\n".join(lines[ctx_start - 1:ctx_end]),
    }


def format_ref(file: str, line_start: int | None, line_end: int | None) -> str:
    """사람이 읽는 참조 표기: "app/main.py:12-34"."""
    if line_start is None:
        return file
    return f"{file}:{line_start}" if line_start == line_end else f"{file}:{line_start}-{line_end}"


def build_code_block(files: dict[str, str], max_chars: int = 12000) -> str:
    """코드베이스를 프롬프트용 블록 하나로. 예산을 넘으면 파일명만 남긴다.

    내용을 짜깁기해 줄이지 않는다 — 잘린 코드로 판정하면 그게 더 나쁘다.
    있다는 사실만 알린다.
    """
    used, included, omitted = 0, [], []
    for path in sorted(files):
        chunk = f"### {path}\n```\n{files[path]}\n```\n\n"
        if used + len(chunk) > max_chars:
            omitted.append(path)
            continue
        included.append(chunk)
        used += len(chunk)
    block = "".join(included)
    if omitted:
        block += f"\n(문자 수 예산 초과로 아래 {len(omitted)}개 파일은 내용을 생략함: {', '.join(omitted)})"
    return block