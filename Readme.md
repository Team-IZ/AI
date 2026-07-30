# Team-IZ AI — `feat/pdf_analysis` (P01 교안 분석)

이 브랜치는 PDF 교안 분석(P01) 도구와 그 배포용 `/docs`를 담고 있습니다.

**실제 문서: [`docs/lab/curriculum-manager/README.md`](docs/lab/curriculum-manager/README.md)**
— 배포 링크 · 구성 · 빠른 실행 · Supabase/Cloudflare Worker 자체 배포 절차 · 이력.

| 경로 | 내용 |
|---|---|
| `docs/lab/` | GitHub Pages가 서빙하는 웹 도구 (`curriculum-manager/`, `p01-runner.js`, `prompt_manifest.json`) |
| `scripts/java_curriculum_nvidia_pipeline.py` | 같은 P01 파이프라인의 CLI 구현 |
| `worker/` | NVIDIA 프록시 Cloudflare Worker (`cd worker && npm test`) |
| `experiments/web_lab/*.sql` | Supabase 스키마 |

배포는 `.github/workflows/pages.yml`이 이 브랜치의 `docs/`, `feat/code_Q&A`, `feat/poc_full`을 하나의 사이트로 조립합니다.

## 결정 기록

> **D-feedback-vendor (2026-07-30, 이 브랜치 자체 변경)**: `scripts/java_curriculum_nvidia_pipeline.py`가
> `sys.path.insert(0, REPO / "feedback")`로 `nvidia_client.py`/`nvidia_key_pool.py`를 import하는데,
> 이 브랜치엔 `feedback/`이 아예 없어서 저장소만 클론하면 `ModuleNotFoundError`로 즉시 죽는 상태였습니다.
> `github.com/popixoxipop-collab/nvidia-build`에서 두 파일을 그대로(무수정) `feedback/`에 복사해
> 해결했습니다 — `feat/code_Q&A`가 `cognition/`/`judgment/`/`feedback/`을 vendoring한 것과 같은 방식입니다.
>   **WHY**: 저장소만 클론해서 바로 실행 가능해야 하고, 다른 사람 소유 프로젝트에 대한 별도 의존성
>     설치 단계를 요구하지 않는 게 이 CLI 스크립트의 원래 취지(`docs/lab/curriculum-manager/README.md`의
>     "자체 배포" 절과 같은 원칙)와 맞습니다.
>   **COST**: 두 파일은 `github.com/popixoxipop-collab/nvidia-build`의 소스이고(`nvidia_client.py` 자체
>     헤더가 이미 "Vendored verbatim ... Last synced: nvidia-build commit 6b57963" 를 명시), 원본이
>     바뀌어도 이 사본은 자동으로 안 따라갑니다 — 필요하면 수동으로 다시 diff해서 반영.
>   **EXIT**: `feedback/` 디렉토리를 지우고 원래 코드를 되돌리려면(비권장 — import가 다시 깨짐)
>     `git rm feedback/nvidia_client.py feedback/nvidia_key_pool.py`.

> **D-refine-issue-type (2026-07-30, 이 브랜치 자체 변경)**: `refine_once()`(`scripts/java_curriculum_nvidia_pipeline.py`)의
> 프롬프트가 `docs/lab/prompt_manifest.json`의 `p01-3`(브라우저가 실제로 쓰는 버전)과 달리
> `issue_type`/`affected_unit_ids` 필드가 없었습니다. 매니페스트 스키마와 맞춰 추가했습니다.
>   **WHY**: 두 구현이 같은 스테이지를 감사(audit)하는데 출력 스키마가 다르면, 나중에 그래프를
>     비교하거나 재사용할 때 조용히 어긋납니다.
>   **COST**: 매니페스트 쪽 `issue_type`은 원래 `p01-runner.js`가 "자동수정 가능한 문제"를 걸러
>     별도 스테이지 `p01-3b`(자동수정)로 라우팅하는 용도인데, **이 Python 파이프라인엔 `p01-3b`에
>     대응하는 함수 자체가 없습니다**(매니페스트 자체 주석: "원본 파이프라인엔 대응 함수 없음"). 즉
>     이 필드들은 여기선 감사 결과에 기록만 되고, 자동으로 뭔가를 고치는 데 쓰이진 않습니다 —
>     `p01-3b`를 포팅하지 않는 한 그 갭은 남아있습니다.
>   **EXIT**: 필드가 필요 없어지면 프롬프트 스키마와 `add_node()` 호출에서 두 필드만 제거하면 됩니다.
