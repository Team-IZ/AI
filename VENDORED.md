# 벤더링된 파일 — 여기서 고치지 마세요

이 브랜치(`feat/poc_full`)는 `feat/code_Q&A`에서 갈라져 나왔고, P02 스캐너를 다시 구현하지
않으려고 그 브랜치의 파일 여러 개를 **무수정 사본(vendored)** 으로 들고 있습니다.

CI가 그 사본들이 원본과 **바이트 단위로 같은지** 매 빌드마다 검사합니다.
그래서 여기서 고치면 — 아무리 옳은 수정이라도 — 빌드가 빨간불이 되고, 그 순간
**네 개 브랜치의 배포가 전부 멈춥니다**(하나의 사이트를 네 브랜치에서 조립하기 때문).

## 잠긴 경로 (드리프트 검사 대상)

| 경로 | 내용 |
|---|---|
| `cognition/` | P02 2-tier 스캐너 (`two_tier_scan.py` 등) — 브라우저가 Pyodide로 **런타임에 읽습니다** |
| `judgment/` | 발견 항목 채점 (`score_findings.py` 등) — 위와 같이 런타임에 읽힘 |
| `shared/` | `lab-core.js`, `db.js`, `p02-engine.js`, `code-locate.js` 등 공용 프런트엔드 |
| `worker/` | Cloudflare Worker (`nvidia-proxy.js`) |
| `prompt_manifest.json` | **저장소 루트의** 것 (p02용). `app/prompt_manifest.json`은 이 브랜치 고유이며 잠겨 있지 않습니다 |
| `webtool_driver.py` | P02 드라이버 |

검사는 `.github/workflows/pages.yml`의 **"Drift-check vendored files"** 스텝이며, 실제로 하는 일은:

```
diff -r -q src-codeqna/<경로> src-poc/<경로>
```

즉 **경로 이름이 양쪽에서 정확히 같아야** 합니다. 파일 내용뿐 아니라 **위치와 이름을 바꾸는 것도**
드리프트로 잡힙니다 — 잠긴 디렉터리 안의 파일은 옮기거나 이름을 바꾸지도 마세요.

### 의도적으로 예외인 것들

- `shared/p03-engine.js` — 이 브랜치에 아예 없음(D2: code-qna 도구의 사본이 `/lab/poc/`에
  두 번째로 배포되는 걸 막으려고 삭제)
- `worker/wrangler.toml`, `shared/config.js` — **일부러 갈라놓았습니다**(D-poc-worker).
  배포 단위 고유값을 담고 있어서입니다: Worker 이름, KV 네임스페이스 id, 큐 이름,
  `LANGSMITH_PROJECT`, 기본 프록시 URL. 바이트 동일성 검사에서는 빠져 있지만, 대신
  **두 브랜치의 값이 서로 충돌하지 않는지**를 같은 스텝이 별도로 검사합니다
  (같은 Worker 이름이나 같은 KV id를 쓰면 빌드 실패).
- `feedback/` — 이 브랜치의 트리에서 완전히 삭제됨(D-feedback1). P04는 한 번도 로드한 적이
  없습니다. 나중에 필요해지면 `feat/code_Q&A`에서 다시 벤더링하고 검사 목록에도 추가하세요.

## 안 쓰는 것처럼 보여도 지우지 마세요

`shared/` 안에는 이 브랜치의 코드가 **참조하지 않는** 파일이 있습니다(예: `shared/code-locate.js`).
겉보기엔 죽은 코드지만 **지우면 안 됩니다** — 드리프트 검사는 `diff -r`이라서 파일이 사라진 것도
차이로 잡고, 네 브랜치의 배포가 다 막힙니다. 마찬가지로 `cognition/`·`judgment/`의 `.py` 파일은
개발용이 아니라 **브라우저가 런타임에 fetch하는 배포 대상**입니다
(`shared/p02-engine.js`의 `REPO_RAW_BASE = "../"`).

## 규칙

> 이 경로들 안의 무언가를 고쳐야 하면,
> **먼저 `feat/code_Q&A`에서 고치고, 그 다음 같은 파일을 여기로 복사하세요.**
> 여기서 직접 고치고 그게 유지되기를 기대하지 마세요 — CI가 되돌리라고 요구합니다.
> 되도록 같은 PR/세션에서 양쪽을 함께 처리하세요.

일부러 이 브랜치에서만 파일을 갈라놓아야 한다면, `pages.yml`의 드리프트 검사 목록(또는
`--exclude`)에서 그 파일을 빼고 **왜 그랬는지 근거를 같이 남기세요**
(`wrangler.toml`/`config.js`가 그 선례입니다 — 바이트 동일성 검사를 빼는 대신 충돌 검사를
넣어서, 검사를 빼는 것이 원래 막으려던 사고를 다시 숨기지 않게 했습니다).

## 이 브랜치 고유 파일 (자유롭게 고쳐도 됨)

`app/`, `db/`, `tests/`, `pages-hub/`, 루트 `index.html`, `Readme.md`, 그리고
`app/prompt_manifest.json`. 단, 아래 여섯 개는 **경로가 고정**입니다 — 내용은 고쳐도 되지만
옮기지는 마세요(`pages.yml` 스모크체크와 vendored 파일의 `../` 상대경로가 이 경로에 의존):

```
app/index.html   app/analysis.html   app/session.html   app/report.html
app/scoring-config.js   app/prompt_manifest.json
```

`app/` 내부 구조에 대해서는 `Readme.md`의 D-poc14를 참고하세요.
