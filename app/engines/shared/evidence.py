""" 코드 스니펫 정규화 + evidence_hash 계산의 유일한 구현

D1 (2026-07-30): Problem.evidence_hash는 "codeSnippet의 sha256 hex 64자"이고
Spring은 이 값을 재계산하지 않고 그대로 신뢰한다(PLAN_FASTAPI_MIGRATION.md D13).
스테이지마다 각자 sha256을 계산하면 개행 문자(CRLF/LF)나 BOM, 트레일링 개행
처리가 미묘하게 달라져 같은 코드인데 해시가 어긋날 수 있다 -- 어긋나도 에러가
안 나고 Spring의 무결성 검사만 조용히 실패한다. 그래서 정규화+해시를 이 모듈
하나로 고정한다. 다른 엔진 스테이지가 생기면 이 함수를 그대로 재사용할 것.
"""
from __future__ import annotations

import hashlib

# 이 이상은 "한 지점"이 아니라 사실상 전체 함수/클래스를 복제하는 것 -- 힌트가
# 가리키는 범위가 모호해진다. locate_symbol이 이 상한 안에서 블록 끝을 찾는다.
BLOCK_MAX_LINES = 40


def normalize_snippet(text: str) -> str:
    """ 줄바꿈/BOM/트레일링 개행을 고정된 규칙으로 통일한다

    - CRLF, CR 전부 LF로
    - 선두 BOM 제거
    - 끝에 개행이 없으면 하나 붙인다(있으면 여러 개를 하나로 줄이지 않음 --
      원문 내용을 함부로 바꾸지 않는 게 원칙, 트레일링 개행 유무만 고정)
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("﻿"):
        normalized = normalized[1:]
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def evidence_hash(snippet: str) -> str:
    """ normalize_snippet을 거친 UTF-8 바이트의 sha256 hex(64자) """
    return hashlib.sha256(normalize_snippet(snippet).encode("utf-8")).hexdigest()


def slice_snippet(file_text: str, line_start: int, line_end: int) -> str:
    """ 1-indexed, 양끝 포함으로 파일에서 스니펫을 잘라낸다

    line_start/line_end는 Problem/ProblemReference가 그대로 쓰는 좌표계와 같다.
    """
    if line_start < 1 or line_end < line_start:
        raise ValueError(f"잘못된 라인 범위: line_start={line_start}, line_end={line_end}")
    lines = file_text.splitlines()
    return "\n".join(lines[line_start - 1 : line_end])


def locate_symbol(file_text: str, symbol: str) -> tuple[int, int] | None:
    """ LLM이 인용한 심볼(함수명/변수명 한 줄)을 파일에서 찾아 (line_start, line_end)로 반환

    D-poc10 원칙 계승: LLM은 줄 번호를 세지 않는다 -- 실제 코드 한 줄을 인용하면
    우리가 그 줄을 찾는다. 못 찾으면 None(호출 측이 이 지점을 버릴 근거로 쓴다).

    1) 정확히 일치하는 줄이 있으면 그 줄부터 들여쓰기가 얕아지는 곳(또는
       BLOCK_MAX_LINES) 전까지를 블록으로 본다.
    2) 없으면 좌우 공백을 정규화(연속 공백 -> 1칸, 앞뒤 트림)한 뒤 재시도한다
       (LLM이 들여쓰기나 트레일링 스페이스를 살짝 다르게 인용하는 경우 대응).
    """
    lines = file_text.splitlines()

    def _try(target: str, normalize: bool) -> int | None:
        for i, line in enumerate(lines):
            candidate = _normalize_ws(line) if normalize else line
            if candidate == target:
                return i
        return None

    idx = _try(symbol, normalize=False)
    if idx is None:
        idx = _try(_normalize_ws(symbol), normalize=True)
    if idx is None:
        return None

    start = idx
    base_indent = _indent_of(lines[start])
    end = start
    limit = min(len(lines) - 1, start + BLOCK_MAX_LINES - 1)
    for j in range(start + 1, limit + 1):
        line = lines[j]
        if line.strip() == "":
            end = j
            continue
        if _indent_of(line) <= base_indent:
            break
        end = j
    return (start + 1, end + 1)  # 1-indexed로 변환


def _normalize_ws(line: str) -> str:
    return " ".join(line.split())


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())
