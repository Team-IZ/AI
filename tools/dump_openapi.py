""" openapi.json 재생성. 백엔드에 넘길 기계용 계약이다.

    python tools/dump_openapi.py            # openapi.json 갱신
    python tools/dump_openapi.py --check    # 갱신이 필요한지만 확인 (CI용, 파일 안 건드림)

**계약을 바꿨으면 반드시 다시 돌린다.** 안 돌리면 백엔드가 옛 스펙으로 구현하고,
그 사실은 통합 시점에야 드러난다. PR 템플릿에도 체크 항목이 있다.

⚠️ **OpenAPI가 표현하지 못하는 규칙이 있다.** 아래 것들은 스펙에 안 나오므로
산문으로 따로 전달해야 한다(PLAN §T10):

  · `ProblemStage`는 4축 전부 questionText 1개 + hints 정확히 2개([1,2] 순서)
  · `ProblemResult.reachedStage`는 stages[].passed를 앞에서부터 센 값과 같아야 한다
  · `/sessions/{id}/answers`의 503은 **같은 clientRequestId로 재전송**하면 된다
  · 세션 1회 = 보고서 3개 (문제 단위)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

OUT = ROOT / "openapi.json"


def main() -> int:
    spec = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
    check = "--check" in sys.argv

    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if old == spec:
        print(f"{OUT.name}: 최신 상태")
        return 0

    if check:
        print(f"{OUT.name}: 갱신이 필요합니다. `python tools/dump_openapi.py`를 돌리세요.",
              file=sys.stderr)
        return 1

    OUT.write_text(spec, encoding="utf-8", newline="\n")
    paths = len(app.openapi()["paths"])
    schemas = len(app.openapi().get("components", {}).get("schemas", {}))
    print(f"{OUT.name} 갱신: 경로 {paths}개 · 스키마 {schemas}개 · {len(spec):,}자")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
