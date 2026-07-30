#!/usr/bin/env python3
""" 두 prompt_manifest.json 사이의 특정 파이프라인이 어긋나지 않았는지 확인 (D5, PR-5)

병행 운영(기존 Worker + 새 FastAPI codemap 엔진) 중에는 같은 프롬프트가 두 곳에
따로 존재할 위험이 있다 -- 한쪽만 고치면 조용히 어긋난다. 이 스크립트는 두
manifest 파일에서 --pipeline으로 지정한 파이프라인 객체를 canonical(키 정렬)
JSON으로 직렬화해 해시를 비교한다.

byte-diff가 아니라 canonical-JSON 해시를 비교하는 이유: 두 매니페스트 파일은
manifest_version이나 다른 파이프라인 구성(예: p04 vs p05)이 원래부터 다를 수
있다 -- 파일 전체를 diff -q로 비교하면 그 정당한 차이 때문에 영구히 실패해서
결국 이 검사 자체를 꺼버리게 된다(.github/workflows/pages.yml 자신의 주석이
경고하는 바로 그 실패 양상). 비교 범위를 지정한 파이프라인 하나로 좁히면
그 안에서는 진짜로 드리프트만 잡아낸다.

사용법:
  tools/check_prompt_drift.py --local app/prompt_manifest.json \
      --remote src-poc/app/prompt_manifest.json --pipeline p04
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _load_pipeline(manifest_path: Path, pipeline: str) -> dict[str, Any] | None:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return data.get("pipelines", {}).get(pipeline)


def check(local_path: Path, remote_path: Path, pipeline: str) -> tuple[bool, str]:
    """ (드리프트 없음, 메시지) """
    local_obj = _load_pipeline(local_path, pipeline)
    remote_obj = _load_pipeline(remote_path, pipeline)

    if local_obj is None and remote_obj is None:
        return True, f"OK: 두 매니페스트 모두 pipeline={pipeline!r}이 없음 -- 비교 대상 아님"
    if local_obj is None or remote_obj is None:
        missing_side = "local" if local_obj is None else "remote"
        return False, f"FAIL: pipeline={pipeline!r}이 {missing_side}에만 없음 -- 한쪽만 만들다 만 상태"

    local_hash = hashlib.sha256(_canonical_json(local_obj).encode("utf-8")).hexdigest()
    remote_hash = hashlib.sha256(_canonical_json(remote_obj).encode("utf-8")).hexdigest()

    if local_hash == remote_hash:
        return True, f"OK: {local_path} / {remote_path}의 pipeline={pipeline!r} 내용 동일 ({local_hash[:12]}...)"
    return False, (
        f"FAIL: {local_path} / {remote_path}의 pipeline={pipeline!r}이 어긋남 "
        f"(local={local_hash[:12]}..., remote={remote_hash[:12]}...) -- "
        f"의도적으로 갈라선 것이면 이 파이프라인을 --pipeline 목록에서 빼고 "
        f"docs/code-importance-map/PARALLEL_RUN_CHECKLIST.md의 PR-5에 그 이유를 적을 것"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", required=True, type=Path)
    parser.add_argument("--remote", required=True, type=Path)
    parser.add_argument("--pipeline", required=True, action="append", help="비교할 파이프라인 키(반복 가능)")
    args = parser.parse_args(argv)

    ok = True
    for pipeline in args.pipeline:
        passed, message = check(args.local, args.remote, pipeline)
        print(message)
        ok = ok and passed

    if not ok:
        print("FAIL: 하나 이상의 파이프라인에서 드리프트 발견", file=sys.stderr)
        return 1
    print("OK: 지정한 모든 파이프라인이 두 매니페스트 사이에서 일치")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
