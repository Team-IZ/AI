""" 7-key 로테이션이 실제로 도는지 독립 검증한다.

D-bench3: NvidiaKeyPool 문서·주석은 "7키 라운드로빈+per-model 슬라이딩 윈도우"라고
주장하지만, 이번 세션에서 그 주장을 코드 리딩만으로 믿고 넘어가지 않는다 --
실제 호출에서 어느 키가 쓰였는지 직접 관찰한다.
  WHY: "문서에 그렇게 써 있다"는 "실제로 그렇게 동작한다"의 증거가 아니다(이
       세션 전체에서 반복해 온 원칙). vendor 코드를 신뢰하되 검증은 우리가 한다.
  COST: 검증용 호출도 실제 NVIDIA API 요금이 든다 -- 그래서 후보 20개에 없는
        모델(mistral-nemotron 등과 무관)을 골라 본 실행(deepseek_v4_flash_
        replacement.py)의 (key, model) 예산과 안 겹치게 한다.
  EXIT: 이 파일은 1회성 검증 스크립트다. 로테이션이 깨졌다는 게 확인되면
        app/llm/vendor/nvidia_key_pool.py는 vendor 소유라 여기서 안 고치고
        상류(nvidia-build) 리포트로 넘긴다(vendor/SOURCE.md 규약).
"""
from __future__ import annotations

import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from deepseek_v4_flash_replacement import _load_keys, _CODE_REVIEWER_ENV, CANDIDATE_MODELS  # noqa: E402

_load_keys(_CODE_REVIEWER_ENV)

sys.path.insert(0, str(_REPO_ROOT / "app" / "llm" / "vendor"))
from nvidia_client import NvidiaRotatingClient  # type: ignore[import-not-found]  # noqa: E402
from nvidia_key_pool import NvidiaKeyPool  # type: ignore[import-not-found]  # noqa: E402

# 본 실행(20개 후보)과 (key, model) 예산이 안 겹치게 후보군 밖 모델을 쓴다.
VERIFY_MODEL = "meta/llama-3.2-3b-instruct"
assert VERIFY_MODEL not in CANDIDATE_MODELS

N_CALLS = 21  # 7키 x 3회 -- 키마다 최소 몇 번은 걸려야 "로테이션이 돈다"고 말할 수 있다

_lock = threading.Lock()
_key_usage: Counter[str] = Counter()
_order: list[str] = []

_pool = NvidiaKeyPool.from_env()
print(f"풀에 로드된 키 개수: {len(_pool._states)}", file=sys.stderr)

_orig_acquire = NvidiaKeyPool.acquire


def _tracking_acquire(self, model, max_wait_s=30.0):
    key = _orig_acquire(self, model, max_wait_s=max_wait_s)
    masked = f"...{key[-4:]}"
    with _lock:
        _key_usage[masked] += 1
        _order.append(masked)
    return key


NvidiaKeyPool.acquire = _tracking_acquire  # type: ignore[assignment]  # 이 프로세스 안에서만, vendor 파일은 안 건드림


def _one_call(i: int) -> str:
    # timeout-guard: allow -- 프로덕션 타임아웃 정책이 아니라 로테이션 검증용
    # 1회성 최소 프롬프트 확인 호출에만 쓰는 값이다. 중앙 설정과 무관.
    client = NvidiaRotatingClient(pool=_pool, timeout_s=20.0)  # timeout-guard: allow
    try:
        client.chat(
            VERIFY_MODEL,
            [{"role": "user", "content": f"{i}번째 검증 호출. 숫자만 하나 답해."}],
            max_tokens=5,  # timeout-guard: allow
        )
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"fail: {type(exc).__name__}"


def main() -> None:
    n_keys = len(_pool._states)
    with ThreadPoolExecutor(max_workers=N_CALLS) as pool:
        futures = [pool.submit(_one_call, i) for i in range(N_CALLS)]
        results = [f.result() for f in as_completed(futures)]

    print(f"호출 {N_CALLS}건 완료: {Counter(results)}", file=sys.stderr)
    print(f"키 풀 크기: {n_keys}", file=sys.stderr)
    print(f"실제 사용된 서로 다른 키 개수: {len(_key_usage)}", file=sys.stderr)
    print(f"키별 사용 횟수: {dict(_key_usage)}", file=sys.stderr)
    print(f"호출 순서(마스킹된 키): {_order}", file=sys.stderr)

    if len(_key_usage) == n_keys:
        print(f"[PASS] {n_keys}개 키가 전부 실제로 로테이션에 사용됐다.", file=sys.stderr)
    else:
        print(f"[FAIL] {n_keys}개 중 {len(_key_usage)}개 키만 사용됐다 -- "
              f"로테이션이 편중돼 있다.", file=sys.stderr)


if __name__ == "__main__":
    main()
