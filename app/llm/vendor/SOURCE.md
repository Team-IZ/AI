# vendor/ — NVIDIA Build 호출 계층 원본

**이 디렉터리의 파일은 고치지 않는다.** 두 파일 자신이 상단 docstring에
*"Vendored verbatim from github.com/popixoxipop-collab/nvidia-build … If you change
rotation/retry behavior, update the source repo first, then re-copy here"*
라고 선언하고 있다. 우리는 그 규칙을 이어받는다.

| | |
|---|---|
| 출처 | `github.com/popixoxipop-collab/nvidia-build` → `AI/_legacy/pipeline/feedback/` 경유 |
| 상류 기준 | nvidia-build 커밋 `6b57963` (D11 per-model fix) |
| 복사 일자 | 2026-07-31 |
| 의존성 | 없음 (Python stdlib만) |

## 파일

| 파일 | 역할 |
|---|---|
| `nvidia_key_pool.py` | 키 N개의 **(키, 모델)별** 슬라이딩 윈도우 예산 관리. 스레드 안전 |
| `nvidia_client.py` | `POST /v1/chat/completions`. 429면 다음 키로 재시도 |

`nvidia_client.py`가 `from nvidia_key_pool import ...`로 플랫 import를 한다.
원본을 안 고치기로 했으므로 `../client.py`가 `sys.path`를 맞춘다
(`app/engines/analysis/vendor/`와 같은 방식).

## 레이트리밋의 실제 단위

무료 티어는 **(키, 모델) 쌍당** 분당 40회다. 키 전체당이 아니다.
키 N개면 **모델당 N × 40**. 원본 D11 주석이 이 착각으로 겪은 사고를 기록해 뒀다 —
한 키로 여러 모델을 돌릴 때 공용 버킷을 쓰면 40콜 만에 포화로 오판한다.

**유료 전환하면 사라질 제약이므로 여기에 아키텍처를 맞추지 않는다.**

## 키 주입

`NvidiaKeyPool.from_env(prefix="NVIDIA_API_KEY_")`가 `NVIDIA_API_KEY_1..N`을
개수·순서 무관하게 수집한다. 값은 `.env`(gitignore)에만 둔다 —
**코드·커밋·로그·에러 메시지 어디에도 키를 옮기지 않는다.**

## 갱신 방법

상류(nvidia-build)를 먼저 고치고 여기로 다시 복사한다. 급하면 `../client.py`에서
우회하고 **우회 사실을 그 자리에 주석으로 남긴다.**
