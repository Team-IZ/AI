""" codemap CLI -- 네트워크 없이 Tier 1 랭킹만 확인하는 Phase 1 인수 게이트

app/engines/attribution/__main__.py와 같은 패턴: python -m app.engines.codemap로 실행.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.engines.codemap import build_code_map_from_repo
from app.engines.codemap.models import CodeMapConfig
from app.engines.shared.signals import from_attribution_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="분석할 저장소 경로")
    parser.add_argument("--attribution-json", help="compute_attribution() 결과 JSON 파일 경로(선택)")
    parser.add_argument("--top", type=int, default=20, help="사람이 읽는 표에 몇 개 보여줄지")
    parser.add_argument("--json", action="store_true", help="전체 결과를 JSON으로 출력")
    args = parser.parse_args(argv)

    attribution = None
    if args.attribution_json:
        raw = json.loads(Path(args.attribution_json).read_text(encoding="utf-8"))
        attribution = from_attribution_result(raw)

    result = build_code_map_from_repo(args.repo, config=CodeMapConfig(), attribution=attribution)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"files scanned: {result['file_count']}, shortlisted: {len(result['shortlist'])}, "
          f"truncated: {len(result['truncated'])}")
    for entry in result["ranked"][: args.top]:
        print(f"{entry['rank']:>3}  {entry['rank_score']:.3f}  {entry['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
