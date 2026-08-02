""" p04 스테이지 하나를 호출하는 공용 경로. llm-stage.js 포팅.

7개 스테이지 전부가 "템플릿 채우기 → LLM 호출 → JSON 파싱"을 그대로 반복한다.
그 반복만 여기 둔다 — 캐싱·정책은 필요해지기 전까지 넣지 않는다.

프롬프트 문자열은 vendor/prompt_manifest.json에서 읽는다. 코드에 박지 않는다.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.llm import client

_VENDOR = Path(__file__).parent / "vendor"

# 매니페스트가 두 개다 — 출처가 다른 PoC 브랜치라 한 파일로 합치지 않는다.
# 합치면 어느 쪽 갱신인지 구분이 사라지고 SOURCE.md의 기준 커밋도 하나만 남는다.
_MANIFEST = _VENDOR / "prompt_manifest.json"            # p04 · feat/poc_full
_CURRICULUM_MANIFEST = _VENDOR / "curriculum_manifest.json"   # p01 · feat/pdf_analysis

# ```json ... ``` 울타리. JSON 모드를 켜도 모델이 가끔 감싸서 준다.
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_BRACES = re.compile(r"\{[\s\S]*\}")


class StageError(RuntimeError):
    """스테이지 실패. 원장에 남길 usage들을 함께 들고 있다(시도마다 1건)."""

    def __init__(self, message: str, usages: list[dict[str, Any]]):
        super().__init__(message)
        self.usages = usages


@dataclass(frozen=True)
class StageResult:
    data: dict[str, Any]
    usages: list[dict[str, Any]]   # 시도마다 1건. 실패한 시도도 들어간다


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _stages_by_id() -> dict[str, dict[str, Any]]:
    """두 매니페스트의 모든 파이프라인을 stage_id 하나로 평탄화한다.

    stage_id에 파이프라인 접두사가 들어 있어(`p01-2`·`p04-5`) 충돌하지 않는다.
    호출부가 "이게 어느 매니페스트에 있나"를 몰라도 되게 하려는 것 — 그건
    vendor 사정이지 우리 로직이 아니다.
    """
    found: dict[str, dict[str, Any]] = {}
    for path in (_MANIFEST, _CURRICULUM_MANIFEST):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for pipeline in (data.get("pipelines") or {}).values():
            for stage in pipeline.get("stages") or []:
                if stage.get("id") and stage.get("kind") == "prompt":
                    found[stage["id"]] = stage
    return found


def get_stage(stage_id: str) -> dict[str, Any]:
    stage = _stages_by_id().get(stage_id)
    if stage is None:
        raise KeyError(f"알 수 없는 stage: {stage_id}")
    return stage


def manifest_version() -> str:
    """DB에 남길 프롬프트 버전. 어느 프롬프트로 만든 결과인지의 근거."""
    return _manifest().get("manifest_version", "unknown")


def _fill(template: str, values: dict[str, Any]) -> str:
    """{placeholder}를 채운다. 값이 없는 자리는 그대로 둔다(원본 fillTemplate과 동일).

    남은 {foo}는 프롬프트에 그대로 나가지만, 필수 자리는 아래 call()이 먼저 막는다.
    """
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def _truncate(values: dict[str, Any], limits: dict[str, int]) -> dict[str, Any]:
    """매니페스트가 정한 상한으로 자른다.

    상한은 컨텍스트 예산이다. 넘기면 모델이 조용히 잘라 읽는 게 아니라 400이 나거나
    뒷부분을 못 본 채 답한다 — 후자가 더 나쁘다(에러 없이 판정만 틀림).
    """
    out = dict(values)
    for key, limit in (limits or {}).items():
        if key in out and isinstance(out[key], str) and len(out[key]) > limit:
            out[key] = out[key][:limit]
    return out


def parse_json(text: str) -> dict[str, Any]:
    """모델 출력에서 JSON 객체를 뽑는다. 실패하면 ValueError."""
    cleaned = _FENCE.sub("", (text or "").strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 앞뒤에 설명 문장이 붙은 경우. 첫 { 부터 마지막 } 까지만 다시 시도한다.
        m = _BRACES.search(cleaned)
        if not m:
            raise ValueError(f"JSON을 찾을 수 없습니다: {cleaned[:200]}")
        return json.loads(m.group(0))


def call(stage_id: str, values: dict[str, Any], *, model_code: str,
         max_attempts: int = 2, timeout_s: float | None = None) -> StageResult:
    """스테이지 하나 실행. 파싱 실패하면 한 번 더 시도한다.

    재시도하는 이유: temperature 0이어도 JSON이 깨져 나오는 경우가 실재한다
    (팀원 실측: 251페이지 실행에서 26청크 중 2건이 배열 중간에서 잘렸다).
    전송 실패(429·타임아웃)는 vendor 클라이언트가 이미 키를 바꿔가며 재시도하므로
    여기서 다시 돌리지 않는다 — 같은 실패를 두 계층에서 세면 예산이 곱해진다.
    """
    stage = get_stage(stage_id)

    for key in stage.get("required_placeholders", []):
        if not str(values.get(key, "")).strip():
            raise ValueError(f"{stage_id}({stage['title']}): 필수 값 누락 — {key}")

    params = {p["key"]: p["default"] for p in stage.get("params", [])}

    # 🔴 문자열 param은 **프롬프트 자리표시자이기도 하다.** 안 채우면 `{course_label}`이
    # 문자 그대로 모델에게 나간다 (2026-08-02 실측: p01-2가 "KT AIVLE School
    # {course_label} curriculum"으로 나갔고, 교안 결과가 한/영 혼재로 돌아왔다).
    # 호출부가 준 값이 우선이고, 안 주면 매니페스트 기본값을 쓴다.
    defaults = {k: v for k, v in params.items() if isinstance(v, str)}
    filled = _truncate({**defaults, **values}, stage.get("truncation", {}))
    messages = [
        {"role": "system", "content": stage["system"]},
        {"role": "user", "content": _fill(stage["user_template"], filled)},
    ]

    usages: list[dict[str, Any]] = []
    budget = client.budget_for(model_code, params.get("max_tokens"))

    for attempt in range(1, max_attempts + 1):
        try:
            result = client.chat(
                model_code, messages,
                max_tokens=budget,
                temperature=params.get("temperature", 0.0),
                response_format={"type": "json_object"},
                timeout_s=timeout_s or client.DEFAULT_TIMEOUT_S,
            )
        except client.LlmError as exc:
            usages.append(exc.usage)
            failure = exc.usage.get("failure_code")

            # 예산이 모자라 잘린 것이면 늘려서 한 번 더. 모델마다 배수를 실측하지 않고도
            # 스스로 맞춰가게 하는 장치다(client.REASONING_TOKEN_MULTIPLIER 주석 참고).
            if failure == "CONTEXT_OVERFLOW" and attempt < max_attempts and budget:
                budget *= 2
                continue

            # 5xx·타임아웃은 일시적이다. 학생이 답을 냈는데 게이트웨이가 한 번 트림했다고
            # 그 턴을 통째로 버리면 안 된다 (실측: mistral 채점에서 HTTP 504).
            # 대신 재시도하는 동안 학생은 계속 기다린다 — 세션 경로의 타임아웃은
            # 배치보다 짧아야 한다(T7c 실측 후 결정).
            if failure in ("PROVIDER_ERROR", "TIMEOUT") and attempt < max_attempts:
                continue

            raise StageError(f"{stage_id}: {exc}", usages) from exc

        usages.append(result.usage)
        try:
            return StageResult(data=parse_json(result.content), usages=usages)
        except (ValueError, json.JSONDecodeError) as exc:
            # 파싱 실패는 전송이 성공한 뒤의 일이라 usage는 SUCCEEDED로 이미 들어갔다.
            # 원장에는 INVALID_JSON으로 남겨야 "왜 이 토큰을 썼나"가 설명된다.
            usages[-1] = {**usages[-1], "status": "FAILED", "failure_code": "INVALID_JSON"}
            if attempt == max_attempts:
                raise StageError(f"{stage_id}: JSON 파싱 실패 — {exc}", usages) from exc
            # JSON이 중간에서 끊겼다면 그것도 예산 문제다.
            if result.raw["choices"][0].get("finish_reason") == "length" and budget:
                budget *= 2

    raise StageError(f"{stage_id}: 도달 불가", usages)  # pragma: no cover