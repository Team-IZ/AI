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
