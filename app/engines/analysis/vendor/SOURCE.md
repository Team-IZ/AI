# vendor/ — 팀원 PoC 규칙부 원본

**이 디렉터리의 파일은 고치지 않는다.** 우리 소유가 아니다.

| | |
|---|---|
| 출처 | `Team-IZ/AI` 브랜치 `feat/poc_full` 의 `cognition/` · `judgment/` · `feedback/` + `app/prompt_manifest.json` |
| 기준 커밋 | `15b02fb` (2026-07-30) |
| 복사 일자 | 2026-07-31 |
| 구성 | `.py` 12개 + `.json` 데이터 19개 + `prompt_manifest.json`(p04-0.2.0) |
| 의존성 | 없음 (Python stdlib만) |

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
$src = "..\ai_poc\poc_full"
$dst = "app\engines\analysis\vendor"
foreach ($d in @("cognition","judgment","feedback")) { Copy-Item -Recurse -Force "$src\$d" "$dst\$d" }
Copy-Item -Force "$src\app\prompt_manifest.json" "$dst\prompt_manifest.json"
```

갱신 후 **이 파일의 기준 커밋·복사 일자를 반드시 같이 고친다.** 안 고치면
"팀원 PoC는 바뀌었는데 서버는 옛 룰"이 조용히 발생한다.

## 버그를 발견하면

`vendor/` 안에서 고치지 않는다. 팀원에게 요청한다. 여기서 고치면 다음 갱신 때 사라진다.
급하면 `../rules.py`(우리 소유)에서 우회하고, **우회 사실을 그 자리에 주석으로 남긴다** —
팀원이 원본을 고치면 우회를 걷어내야 하기 때문이다.

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
