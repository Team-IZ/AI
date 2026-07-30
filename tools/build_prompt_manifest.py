#!/usr/bin/env python3
""" 스테이지-per-YAML -> app/prompt_manifest.json 빌드 스크립트 (D3-2)

YAML이 유일한 소스다. app/prompt_manifest.json은 이 스크립트가 생성하는
산출물이며 손으로 고치지 않는다 -- --check가 그 드리프트를 CI에서 잡는다.

읽기-수정-쓰기(read-modify-write)로 동작한다: --out에 이미 다른 파이프라인
(예: p04)이 있으면 그건 건드리지 않고 --pipeline으로 지정한 파이프라인만
교체한다 -- 두 파이프라인이 한 파일을 공유해도 서로의 스테이지를 지우지 않는다.

롤백 이야기(D3의 이 스크립트를 만든 이유): 이 접근이 안 맞으면 prompts/ 디렉터리를
지우고 손으로 관리하는 JSON만 남기면 된다 -- 모든 소비자(app/engines/shared/
prompts.py::load_stage)는 JSON만 읽으므로 그 쪽은 전혀 안 바뀐다.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# 스테이지 객체에 그대로 옮겨 적는 키 -- app/prompt_manifest.json(origin/feat/poc_full)의
# 실제 스테이지 키 집합과 1:1 대응(확인: id/title/kind/function/system/system_note/
# user_template/required_placeholders/optional_placeholders/truncation/params).
_STAGE_KEYS = (
    "id", "title", "kind", "function", "system", "system_note",
    "user_template", "required_placeholders", "optional_placeholders",
    "truncation", "params",
)


def _load_stage_yaml(path: Path) -> tuple[str, dict[str, Any]]:
    """ YAML 파일 -> (pipeline, stage dict). stage dict는 _STAGE_KEYS만 담는다
    ("pipeline" 키는 이 파일이 어느 파이프라인 소속인지 판별하는 메타데이터일
    뿐, 스테이지 객체 자체에는 들어가지 않는다 -- prompt_manifest.json의 실제
    스테이지 객체에 pipeline 키가 없는 것과 대응). """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "pipeline" not in data:
        raise ValueError(f"{path}: 'pipeline' 키가 없습니다 -- 어느 파이프라인 소속인지 알 수 없음")
    pipeline = data["pipeline"]
    stage = {k: data[k] for k in _STAGE_KEYS if k in data}
    for required in ("id", "title", "kind", "function", "system", "user_template"):
        if required not in stage:
            raise ValueError(f"{path}: 필수 키 누락 -- {required}")
    return pipeline, stage


def build_pipeline_object(
    src_dir: Path, pipeline: str, *, label: str, source_files: list[str], has_llm_calls: bool = True
) -> dict[str, Any]:
    """ src_dir 아래 모든 *.yaml 중 pipeline이 일치하는 것만 모아 stages를 id순 정렬해 반환 """
    stages: list[dict[str, Any]] = []
    for path in sorted(src_dir.glob("*.yaml")):
        file_pipeline, stage = _load_stage_yaml(path)
        if file_pipeline == pipeline:
            stages.append(stage)
    stages.sort(key=lambda s: s["id"])
    if not stages:
        raise ValueError(f"{src_dir} 아래에 pipeline={pipeline}인 YAML이 하나도 없습니다")
    return {
        "label": label,
        "source_files": source_files,
        "has_llm_calls": has_llm_calls,
        "stages": stages,
    }


def merge_into_manifest(
    existing: dict[str, Any] | None, pipeline: str, pipeline_obj: dict[str, Any], *, manifest_version: str | None
) -> dict[str, Any]:
    """ 읽기-수정-쓰기: 기존 manifest의 다른 pipeline은 그대로 두고 지정한 것만 교체 """
    manifest: dict[str, Any] = dict(existing) if existing else {
        "manifest_version": manifest_version or "0.0.0",
        "generated_from_commit_note": "",
        "pipelines": {},
        "shared": {},
    }
    if manifest_version:
        manifest["manifest_version"] = manifest_version
    manifest.setdefault("pipelines", {})
    manifest["pipelines"][pipeline] = pipeline_obj
    return manifest


def _dump(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="스테이지 YAML들이 있는 디렉터리")
    parser.add_argument("--out", required=True, help="app/prompt_manifest.json 경로")
    parser.add_argument("--pipeline", required=True, help="빌드할 파이프라인 키, 예: p05")
    parser.add_argument("--label", default="", help="pipelines.<pipeline>.label")
    parser.add_argument("--source-file", action="append", default=[], help="pipelines.<pipeline>.source_files에 추가 (반복 가능)")
    parser.add_argument("--manifest-version", default=None, help="top-level manifest_version. 생략하면 기존 값 유지")
    parser.add_argument("--check", action="store_true", help="쓰지 않고 기존 파일과 일치하는지만 확인, 다르면 exit 1")
    args = parser.parse_args(argv)

    src_dir = Path(args.src)
    out_path = Path(args.out)

    pipeline_obj = build_pipeline_object(
        src_dir, args.pipeline, label=args.label, source_files=args.source_file
    )

    existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
    manifest = merge_into_manifest(existing, args.pipeline, pipeline_obj, manifest_version=args.manifest_version)
    new_text = _dump(manifest)

    if args.check:
        old_text = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if new_text != old_text:
            diff = difflib.unified_diff(
                old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=str(out_path), tofile=f"{out_path} (built from {src_dir})",
            )
            sys.stdout.writelines(diff)
            print(f"FAIL: {out_path} is out of date relative to {src_dir}/*.yaml", file=sys.stderr)
            return 1
        print(f"OK: {out_path} matches {src_dir}/*.yaml")
        return 0

    out_path.write_text(new_text, encoding="utf-8")
    print(f"wrote {out_path} (pipeline={args.pipeline}, {len(pipeline_obj['stages'])} stage(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
