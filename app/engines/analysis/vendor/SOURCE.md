# vendor/ — 팀원 PoC 규칙부

🔴 **2026-08-02 정책 변경. "무수정"이 아니다.**

**고쳐도 된다. 단, 고친 것은 전부 [`PATCHES.md`](PATCHES.md)에 남긴다.**

우리 역할이 바뀌었기 때문이다 — PM 요청·백엔드 요청·실측 성능 사이를 조정하는 자리이고,
그 조정이 프롬프트·규칙 수준에서 필요해질 때 팀원 회신을 기다리면 그동안 아무것도 못 한다.

**대신 대가가 생겼다.** 팀원은 계속 개발 중이고 갱신은 "복사"다(§갱신 방법).
**복사하면 우리 수정이 조용히 사라진다.** 그래서 규칙 셋:

| | |
|---|---|
| 1 | 수정하면 **`PATCHES.md`에 항목을 추가한다.** 무엇을·왜·동작이 어떻게 달라지는지·재적용 방법 |
| 2 | 갱신(복사) 후 **`PATCHES.md`의 모든 항목을 다시 적용한다** |
| 3 | `tests/test_vendor_patches.py`가 **각 패치가 살아 있는지 검사한다.** 갱신이 패치를 지우면 테스트가 깨져 알려준다 |

3번이 핵심이다. 사람 기억에 맡기면 반드시 놓친다.

**상류에 반영할 수 있는 것은 팀원에게도 요청한다.** 우리 패치가 영원히 유지되는 것보다
원본이 고쳐지는 쪽이 낫다 — 그러면 패치를 지울 수 있다. `PATCHES.md`의 "상류 반영" 칸이
그 상태를 추적한다.

**출처가 두 브랜치다.** 갱신 주기가 서로 다르므로 표도 따로 본다.

| | 규칙부 + p04 | 교안 p01 |
|---|---|---|
| 출처 | `feat/poc_full` 의 `cognition/`·`judgment/`·`feedback/` + `app/prompt_manifest.json` | `feat/pdf_analysis` 의 `docs/lab/prompt_manifest.json` |
| 받는 파일 | 위 3개 디렉터리 + `prompt_manifest.json` | `curriculum_manifest.json` (개명해 둔다) |
| 기준 커밋 | `15b02fb` (2026-07-30) | `5c7f84f` (2026-08-02) |
| 복사 일자 | 2026-07-31 | 2026-08-02 |
| 구성 | `.py` 12개 + `.json` 데이터 19개 + 매니페스트(p04-0.2.0) | 매니페스트 1개(p01-1.0.0) |
| 의존성 | 없음 (Python stdlib만) | 없음 (프롬프트 문자열뿐) |

⚠️ **`feat/poc_full`의 상류가 `756c4cb`로 앞서 있다**(Tier B 제거). 동기화는 선별 로직
교체와 함께 판단한다 — `PLAN_FASTAPI_MIGRATION.md` §T10-B §7-8.

**`stages.py`가 두 매니페스트를 함께 읽는다.** stage id에 파이프라인 접두사가 있어
(`p01-2`·`p04-5`) 충돌하지 않으므로 호출부는 어느 파일에 있는지 몰라도 된다.
**한 파일로 합치지 않는다** — 합치면 어느 쪽 갱신인지 구분이 사라진다.

## `prompt_manifest.json`은 계약이다

p04-1~7의 system·user_template·`max_tokens`·`temperature`·truncation 상한이 전부 여기 있다.
**프롬프트 문자열을 코드에 박지 않는다** — 박으면 팀원이 프롬프트를 고쳐도 서버가 옛 문구로 돌고,
"같은 파이프라인"이라는 전제가 조용히 깨진다. `../stages.py`가 이 파일을 읽어 채운다.

`temperature: 0.0`에 `"locked": true`가 붙은 스테이지가 있다(p04-1). 재현성 요구다 —
같은 제출물이 같은 분석 문서를 내야 비교가 성립한다. **오버라이드하지 않는다.**

## 왜 그대로 두는가

12개 파일이 `sys.path.insert(os.path.dirname(__file__))` + 플랫 import(`from subrubric import ...`)로
서로를 부른다. 패키지 상대 import로 바꾸면 12개를 전부 손대야 하는데, **팀원이 이 파일들을
계속 고치는 중이다.** 원본을 유지하면 갱신이 병합이 아니라 **복사**로 끝난다.

JSON 데이터 파일도 `os.path.dirname(__file__)` 기준으로 열리므로 **디렉터리 구조를 바꾸면 안 된다.**

## 갱신 방법

```powershell
# AI/ 에서. ai_poc/ 워크트리를 먼저 최신화해 둘 것
$dst = "app\engines\analysis\vendor"

# 규칙부 + p04
$src = "..\ai_poc\poc_full"
foreach ($d in @("cognition","judgment","feedback")) { Copy-Item -Recurse -Force "$src\$d" "$dst\$d" }
Copy-Item -Force "$src\app\prompt_manifest.json" "$dst\prompt_manifest.json"

# 교안 p01 (다른 브랜치 · 개명해서 받는다)
Copy-Item -Force "..\ai_poc\pdf\docs\lab\prompt_manifest.json" "$dst\curriculum_manifest.json"
```

**복사는 우리 패치를 덮어쓴다.** 그래서 갱신은 세 단계다:

```
1. 위 Copy-Item 실행                     ← 우리 수정이 전부 날아간다
2. PATCHES.md의 항목을 순서대로 재적용    ← 각 항목의 "재적용" 절이 방법을 적어둠
3. pytest tests/test_vendor_patches.py   ← 하나라도 빠지면 여기서 잡힌다
```

갱신 후 **이 파일의 기준 커밋·복사 일자를 반드시 같이 고친다.** 안 고치면
"팀원 PoC는 바뀌었는데 서버는 옛 룰"이 조용히 발생한다.

**재적용 중 충돌이 나면**(팀원이 같은 자리를 고쳤다) 그 자리는 팀원 것을 따르고
`PATCHES.md` 항목을 **"상류 반영됨"으로 닫는다.** 두 수정을 겹쳐 쌓지 않는다.

## 버그를 발견하면

**고쳐도 된다**(2026-08-02 정책 변경). `PATCHES.md`에 남기고 팀원에게도 알린다.

**단, 우리 소유 코드로 우회할 수 있으면 그쪽이 먼저다.** `../rules.py`·`../scoring.py`처럼
우리 파일에서 처리하면 갱신 때 사라지지 않고 재적용도 필요 없다. vendor를 직접 고치는 것은
**우리 쪽에서 도저히 표현이 안 될 때**만이다 — 예: 모델 응답 필드를 늘리는 것은
프롬프트의 JSON 스키마를 고쳐야 해서 우리 쪽에서 못 한다(P-1).

## 진입점

```python
scan = two_tier_scan.scan(repo_root)             # 구조 스캔
result = score_findings.score(scan, repo_root)   # {hub, findings: [...]}
```

나머지 10개 모듈은 `score()`가 내부에서 부른다. 직접 호출할 일이 없다.
원본의 `webtool_driver.py`(Pyodide 진입점)는 **가져오지 않았다** — 파라미터 오버라이드는
브라우저 실험 패널용이라 서버에 필요 없다.

## 재현성

`rank_weights/rank_weights.json` · `idioms/*/idiom_patterns.json` ·
`subrubric_weights/*/weights.json` 등 데이터 파일이 결과를 바꾼다.
`Problem.extractorVersion`("이 문제를 뽑은 룰 버전")이 뜻을 가지려면
**기준 커밋 + 데이터 파일 해시**가 그 값에 반영돼야 한다.
