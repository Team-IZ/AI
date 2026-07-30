#!/usr/bin/env bash
# p05(codemap) 파이프라인의 build_prompt_manifest.py 호출 인자를 한 곳에 고정한다.
#
# WHY: --label/--source-file/--manifest-version 같은 인자를 CI(.github/workflows/ci.yml)와
# 로컬 개발자 터미널에 각각 따로 적어두면, 둘 중 하나만 고치는 실수로 --check가 가짜로
# 실패(또는 가짜로 통과)할 수 있다. 이 스크립트 하나가 유일한 소스라 인자가 어긋날 수 없다.
#
# 사용:
#   tools/rebuild_codemap_manifest.sh          # app/prompt_manifest.json을 다시 쓴다
#   tools/rebuild_codemap_manifest.sh --check  # 쓰지 않고 최신인지만 확인 (CI가 씀)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

python3 tools/build_prompt_manifest.py \
  --src app/engines/codemap/prompts \
  --out app/prompt_manifest.json \
  --pipeline p05 \
  --label "코드 중요도 맵" \
  --source-file app/engines/codemap/crew.py \
  --manifest-version p05-0.1.0 \
  "$@"
