// "무엇이 중요한가"를 후보로 모으고(1단계) -> 실제 소스와 대조해 근거를 확정하고(2단계) ->
// 점수로 줄 세우고(3단계) -> 상위 K개만 심층 분석에 태우는(4단계) 파이프라인.
//
// ─────────────────────────────────────────────────────────────────────────────
// D-poc13 (2026-07-31): 코드 분석 문서 파이프라인 재설계 -- 설계 + 1~3단계 프로토타입
// ─────────────────────────────────────────────────────────────────────────────
//
// 문제: p04-1(코드 분석 문서)은 지금 "코드베이스 전체를 12,000자 예산에 욱여넣고 LLM 한 번
// 호출"이다(poc-engine.js:81의 CodeFragment.buildCodeBlock). 예산 안에 뭘 넣을지는
// Object.keys(files).sort() -- 알파벳순이고, 중요도 신호가 0이다. 알파벳으로 늦은 핵심
// 파일이 잘리고 알파벳으로 이른 사소한 파일이 예산을 먹는다. 그 상태로 만든 분석 문서가
// decision_points -> 문제 선정 -> 질문/힌트 전부의 상류다.
//
// 방향: "전체를 한 번에 얕게" 대신 "중요한 곳을 골라 각각 깊게". 단, 이 저장소가 이미
// D-poc6/D-poc10에서 확립한 원칙 -- **LLM의 주장과 산정된 사실을 분리한다** -- 를 위치
// 지목뿐 아니라 "무엇이 중요한가" 판단 전체에 적용한다.
//
// ── 1단계: 후보 식별 -- 하이브리드(LLM + 구조 + 스캐너), 새 LLM 호출 0회 ───────────
//   WHY 하이브리드인가: 세 신호가 서로 다른 실패 모드를 가진다.
//     - LLM 제안(p04-1의 decision_points): "판단이 개입된 지점"처럼 의미론적인 건 잘 찾지만
//       지어낼 수 있다(D-poc6). -> 2단계 grounding이 걸러낸다.
//     - 구조 신호(fan_in): 절대 지어내지 않지만 "많이 참조됨 != 이해도 검증에 좋음"이다
//       (허브 유틸 파일이 항상 1등으로 올라온다).
//     - P02 finding: 이미 판정 블록이 rank_score까지 매겨둔 것(judgment/importance_rank.py
//       D194)인데, 파일 단위라 코드 위치가 없다.
//     한 신호만 쓰면 그 신호의 실패 모드를 그대로 물려받는다. 세 개를 같은 후보 형태로
//     모아 3단계에서 가중합하면, 두 소스가 같은 위치를 지목한 것 자체가 신호가 된다
//     (rankCandidates의 agreement 항).
//   WHY 새 LLM 호출이 0회인가: 세 소스 전부 **이미 계산되어 있다**.
//     - decision_points: p04-1이 지금도 반환한다(app/prompt_manifest.json).
//     - fan_in: P02Engine.run()의 result.scan.tier_a_structural.fan_in -- Pyodide가 이미
//       Tier A 구조 스캔에서 만든 값이다(cognition/two_tier_scan.py:327).
//     - finding.rank_score/rank: judgment/importance_rank.py::apply_rank()가 score() 안에서
//       이미 붙여준다(score_findings.py:534).
//     즉 1단계는 "새로 계산"이 아니라 "이미 있는 세 결과를 한 형태로 모으기"다. 후보
//     식별 전용 LLM 호출을 새로 파는 설계도 가능했지만, 그건 4단계 fan-out이 이미 감당해야
//     할 NVIDIA 호출 예산(아래)을 후보 식별에까지 또 쓰는 것이라 채택하지 않았다.
//   COST: fan_in dict의 키가 **basename**이다(two_tier_scan은 os.path.basename으로 집계 --
//     shared/p02-engine.js:148의 같은 경고 참조). 서로 다른 폴더의 동명 파일은 fan_in이
//     합쳐져 있다. 여기서 고치지 않는다(스캐너 쪽 계약이다) -- rank_evidence에 fan_in을
//     그대로 노출해서, 이상한 순위가 나왔을 때 "합쳐진 basename 때문"임을 사람이 볼 수 있게만 한다.
//   EXIT: 후보가 구조적으로 부족한 제출물(파일 3개짜리 과제 등)이면 세 소스 전부 빈약하다.
//     그때는 후보 식별 전용 LLM 호출을 하나 추가하는 게 맞다 -- collectCandidates()에
//     source:"llm2" 생산자를 하나 더 붙이면 되고, 2~4단계는 그대로 재사용된다.
//
//   ── D-tierb3 (2026-07-31): 세 소스 중 "P02 finding"이 Tier B 제거로 얼마나 얇아졌는가 ──
//   feat/code_Q&A의 D-tierb1이 Tier B(정규식 위험 트리거 스캔)를 없앴고, 벤더링된
//   cognition/judgment가 바이트 동일해야 하므로(.github/workflows/pages.yml의
//   drift-check) 이 브랜치도 같이 제거됐다. 그 결과가 P04에 어떻게 오는지는 code_Q&A의
//   D-tierb2(랭킹 축 이야기)와 **다른 문제**라 여기 따로 적는다.
//   이 파일은 벤더링 대상이 아니므로(app/은 drift-check 목록 밖) 이 브랜치 고유 사실을
//   적을 수 있는 유일한 자리이기도 하다 -- judgment/에 적으면 그게 곧 드리프트다.
//
//   측정(code_Q&A와 **같은 고정 fixture**를 이 브랜치의 자기 코드로 다시 돌린 것 --
//   숫자를 그쪽에서 베끼지 않았다. JS 6파일 = entry/hub/peer 3 + 중복정의 1쌍,
//   과거 Tier B 3트리거 전부 발동):
//     [judgment 산출물]  제거 전: finding 5건 / 제거 후: 2건
//       제거 전 rank_score = 8.667, 8.0, 6.667, 5.667, 4.667
//       제거 후 rank_score = 6.667, 5.667  (살아남은 2건의 값과 상대 순서는 제거 전과 동일)
//     [이 파일에 도착하는 것]  collectCandidates()는 file이 없는 finding을 건너뛰므로(D76,
//       repeated-pattern이 여기 해당) source:"finding" 후보는
//         제거 전: 4개  ->  제거 후: **1개**
//       사라진 3개가 전부 tier-b-risk 계열이었다. 즉 이 소스의 산출량이 이 fixture에서 75%
//       줄었다.
//     [3단계 finding_rank 항]  finding_rank = f.rank_score / 코퍼스 최대값이라
//       제거 전 정규화값 = 1.000 / 0.923 / 0.654 / 0.538  (스프레드 0.462)
//       제거 후 정규화값 = 1.000                            (스프레드 **0.000**)
//
//   WHY 그래도 코드를 안 바꾸는가: 스프레드 0.000은 "이 항이 틀렸다"가 아니라 "이 fixture에서
//   finding 소스 후보가 1개뿐"이라는 뜻이다. 후보가 2개 이상인 제출물에서는 이 항이 그대로
//   판별력을 되찾는다(rank_score 자체는 여전히 finding마다 다르다 -- D-tierb2가 상수가 됐다고
//   한 건 rank_score가 아니라 그 **안의 risk 축**이다. 둘을 헷갈리지 말 것).
//   가중치를 지금 조정하는 건 RANK_WEIGHTS_PROVENANCE가 금지하는 "측정 없는 감"이다.
//   COST: (1) 후보 풀에서 finding 소스의 기여가 실제로 얇아졌으므로, agreement 항(두 소스가
//   같은 위치를 지목하면 가점)이 성립할 기회도 같이 줄었다 -- llm/structural 두 소스에
//   더 의존하게 된다. (2) 없어진 3건은 전부 "위험" 성격 후보였다. P04가 위험 지점을
//   후보로 올릴 확률은 이제 p04-1 LLM이 그걸 decision_point로 짚어주느냐에만 달려 있다.
//   (3) 이 측정은 **6파일짜리 고정 fixture 1개**다. 실제 제출물 분포가 아니므로 "75% 감소"를
//   일반화하지 말 것 -- 방향(줄었다)은 확실하고 크기는 미검증이다.
//   EXIT/재보정: 실제 제출물로 Precision@3을 재는 스프린트에서 finding 소스의 기여도를
//   함께 측정한다. 그때도 기여가 미미하면 RANK_WEIGHTS.finding_rank를 실측값으로 내린다 --
//   그 전에 손으로 내리지 않는다.
//
// ── 2단계: grounding -- CodeFragment.locateSymbol() 그대로 재사용(복제 아님) ────────
//   WHY 복제가 아니라 호출인가: locateSymbol은 이 저장소에서 "LLM이 세지 않는다"는 원칙의
//     구현체 그 자체다(D-poc10). 복제하면 들여쓰기 휴리스틱/정규화 규칙이 두 벌이 되고,
//     한쪽만 고쳐지는 드리프트가 정확히 이 저장소가 .github/workflows/pages.yml의
//     drift-check로 막고 있는 실패 모드다. 이 모듈은 locateSymbol을 호출만 하고, 위치
//     산정 로직은 한 줄도 다시 쓰지 않는다.
//   COST: locateSymbol은 **첫 번째** 매치 줄만 돌려준다(code-fragment.js:56). 심볼이 짧으면
//     (예: P02 tier-b finding의 matched_text는 AUTH_KEYWORDS.search().group(0) -- "uid" 같은
//     3글자일 수 있다) 엉뚱한 줄에 조용히 grounding될 수 있다. LLM이 코드 한 줄을 통째로
//     인용하는 기존 경로에서는 거의 안 터지지만, 이 모듈은 LLM이 아닌 소스(finding
//     matched_text, 구조 후보)도 심볼로 쓰므로 새로 노출되는 위험이다.
//     -> countSymbolMatches()로 **매치 개수를 우리가 따로 센다**. 규칙:
//        - 매치 0 -> 버린다(기존 D-poc6/D-poc10 규율 그대로).
//        - 매치 1(정확) -> confidence 1.0.
//        - 매치 2개 이상(정확) -> confidence 0.6으로 강등. **버리지는 않는다** -- 그 코드는
//          실제로 존재하고, 다만 "이 줄이 맞는 그 줄인지"가 불확실할 뿐이다. 존재하지 않는
//          코드를 보여주는 것(D-poc6가 막는 것)과는 다른 종류의 문제라 다르게 다룬다.
//        - 공백 정규화로만 매치 -> confidence 0.5.
//        - MIN_SYMBOL_CHARS 미만 -> 심볼로 쓰지 않는다(파일 단위 후보로 강등). 3글자 심볼은
//          매치 개수를 세도 의미가 없다.
//     매치 개수 세는 규칙만 locateSymbol과 중복된다(.includes + normalizeForMatch). 위치
//     산정은 여전히 locateSymbol 단독 -- 두 함수가 다른 답을 낼 여지는 "몇 번째 매치인가"뿐이다.
//   EXIT: 언젠가 locateSymbol 자체가 matchCount를 반환하게 되면 countSymbolMatches()를 지우고
//     그 값을 쓰면 된다. 그 변경은 code-fragment.js의 기존 호출자에게 영향이 없다(반환 객체에
//     필드 추가일 뿐).
//
// ── 3단계: 랭킹 -- D194(judgment/importance_rank.py) 형태를 그대로 따른다 ───────────
//   rank_score = Σ(weight_i * term_i) / Σweight_i,  모든 term은 0..1로 정규화.
//   terms:
//     llm_proposed    p04-1이 지목했으면 1 (의미론적 신호)
//     finding_rank    finding.rank_score / 코퍼스 내 최대값 (판정 블록의 3축 가중합 재사용)
//     fan_in          fan_in[basename] / 최대 fan_in (구조 신호)
//     ground          2단계 confidence (1.0 / 0.6 / 0.5) -- 근거가 약하면 순위도 내려간다
//     teach_linked    related_teach가 실제 teaches에 있으면 1 (이번 교안에서 검증할 축인가)
//     agreement       서로 다른 소스 2개 이상이 같은 위치를 지목했으면 1
//   WHY 이 형태인가: judgment/importance_rank.py가 이미 이 저장소의 랭킹 관례다 --
//     (a) 가중치는 전부 1.0에서 시작하고 "unmeasured/provisional"이라고 정직하게 표시한다,
//     (b) rank_evidence(weights/terms/tie_break_depth)를 항목마다 붙여 다른 항목과 대조하지
//     않고도 자기 순위를 설명할 수 있게 한다, (c) 완전순서 타이브레이크로 재현성을 보장한다.
//     같은 저장소 안에서 랭킹 관례를 두 개 만들 이유가 없다.
//   COST: **가중치 6개는 측정값이 아니다**(CLAUDE.md §13 -- 직관으로 숫자를 넣지 않는다는
//     규칙의 정직한 준수: 넣을 데이터가 아직 없으므로 "임의값"이라고 표시하고 재보정 조건을
//     명시한다). 등가중은 "어느 신호가 더 중요한지 모른다"의 정직한 표현이지, 최적값 주장이 아니다.
//   EXIT/재보정 조건: judgment/rank_weights/rank_weights.json이 D194에서 이미 채택한 방식 --
//     라벨이 생기면 코드가 아니라 데이터를 갈아끼운다. 여기도 같게 간다. 재보정 트리거는
//     "실제 제출물 20건에서 top-3 후보가 사람이 고른 top-3과 얼마나 겹치는가(Precision@3)"를
//     한 번이라도 측정했을 때. 그 전까지 가중치를 감으로 바꾸지 말 것.
//
// ── 4단계: 병렬 fan-out -- **설계만. 이 커밋에서 구현/배선하지 않는다** ──────────────
//   구조: 상위 K개 각각에 대해 "이 위치 하나만" 파고드는 LLM 호출(가칭 p04-1b) ->
//   Promise.allSettled로 회수 -> 성공분만 analysisDoc.decision_points[i].deep_dive에 병합.
//   WHY 굳이 병렬인가: 순차면 K개 × (관측된 꼬리 지연)이 그대로 벽시계에 더해진다. 이 세션에서
//     직접 측정된 NVIDIA 지연은 같은 모델이 같은 프롬프트에서 ~2s ~ 92s, 그리고 150s 안에
//     아예 무응답(shared/llm.js 파일 헤더 D-H에 기록된 그대로)까지 흔들린다. K=3 순차면
//     최악이 3연속 꼬리다.
//   COST(정직하게, 정량으로):
//     (a) 호출 수: K개 동시 제출 = 클라이언트 요청 K개. 그런데 worker/nvidia-proxy.js는
//         제출 1건당 서버 측에서 최대 MAX_ATTEMPTS=3회까지 재시도한다(D-I). 즉 **실제 NVIDIA
//         요청은 최대 3K회**이고, 그 재시도는 브라우저에 안 보인다(shared/llm.js D159가 명시).
//         K=3이면 최악 9회. 자유 티어 상한은 ~40rpm(shared/traffic-rate.js의
//         RATE_LIMIT_THRESHOLD, nvidia-keypool-guard.py 문서값)이고, p04 분석 단계는 이미
//         p04-1/p04-2(요구사항 수만큼)/p04-3/p04-4(문제 3개, 재생성 포함)/p04-7로 10회 이상을
//         쓴다. 여기에 무경계 fan-out을 얹으면 상한을 실제로 밟는다 -- 26-way 병렬 청크 버스트가
//         무관한 후속 호출에서 429를 유발한 전례가 이미 기록돼 있다(nvidia-proxy.js D159).
//     (b) 실패 확률: 호출 1건이 나쁜 구간에 걸릴 확률을 p라 하면 K개 중 최소 1개가 실패할
//         확률은 1-(1-p)^K. 관측된 무응답 빈도를 보수적으로 p=0.1로 잡으면 K=3에서 27%,
//         K=6에서 47%다. **K를 키울수록 "적어도 하나는 깨진다"가 예외가 아니라 기본값이 된다.**
//         그래서 부분 실패는 사고가 아니라 정상 경로로 설계해야 한다.
//     (c) 벽시계 이득: 동시성 C일 때 대략 ceil(K/C) × 꼬리지연. K=3,C=2면 2×꼬리, 순차면 3×꼬리 --
//         꼬리 90s 기준 180s vs 270s. **이득이 극적이지 않다.** 병렬화의 진짜 이유는 속도보다
//         "한 번의 거대 프롬프트"를 "작고 집중된 프롬프트 여러 개"로 쪼개서 예산 절벽을 없애는
//         것이다. 속도만 노린다면 이 복잡도는 수지가 안 맞는다.
//   설계 규칙(resolveFanoutPlan()으로 구현 -- 순수 함수, 호출 없음):
//     1. 동시성 상한 C = 2 (기본). shared/p02-engine.js의 CONCURRENCY=6은 GitHub blob fetch
//        기준이라 여기 근거로 쓸 수 없다 -- NVIDIA는 rpm 상한이 있고 재시도가 보이지 않는다.
//     2. K 상한 = 5. 그 이상은 (b)의 실패 확률이 50%를 넘고 (a)의 3K가 상한의 1/3을 먹는다.
//     3. 호출 직전 DebugTraffic.getCurrentRate()로 현재 rpm을 보고 **K를 깎는다**.
//        여유(headroom) = threshold - count, 안전 제출 수 = floor(headroom / 3) (제출 1건 =
//        최대 3 NVIDIA 요청). 탭 기준 카운트(isServerWide=false)면 다른 팀원 트래픽이 안
//        잡히므로 여유를 절반으로 더 깎는다.
//        WHY P03의 D181과 반대 방향인가: P03은 트래픽이 높을 때 maxAttempts를 **올린다**(5).
//        P03은 동시 호출이 1건뿐이라 깎을 K가 없고, 실패하면 인터뷰가 멈추므로 재시도를 사는 게
//        맞다. fan-out은 반대로 깎을 K가 있고, 실패해도(아래 4번) 결과가 오늘 수준으로 내려갈
//        뿐이다. 그러므로 부하를 사지 말고 **덜어낸다**. maxAttempts는 워커 기본값(3) 유지.
//     4. 부분 실패 = 정상 경로. Promise.all 금지(하나 reject되면 나머지 성공분까지 버린다).
//        allSettled로 받고, 실패한 후보는 **오늘과 똑같이** 근거 코드 파편 + p04-1의
//        why_it_matters 한 줄로 렌더하고 "심층 분석 없음"만 표시한다. 최악의 fan-out 결과가
//        오늘의 결과와 같도록 하는 게 이 설계의 신뢰성 계약이다 -- 이 저장소 전체의 관례
//        (DB 저장 best-effort, LangSmith 실패 무시, 힌트 flagged 통과)와 같은 철학이다.
//     5. K=0으로 깎이면 fan-out 자체를 건너뛴다. 이것도 실패가 아니라 정상 종료다.
//
// ── 어느 저장소에 넣는가 ──────────────────────────────────────────────────────────
//   **P04(feat/poc_full): 1~4단계 전부.** 분석 문서 단계(p04-1)를 소유하고, locateSymbol이
//     이미 여기 있고, 예산 절벽(12,000자 알파벳 채우기)이 실제로 여기서 발생한다.
//   **P03(feat/code_Q&A): 2단계만. 1/3/4단계는 넣지 않는다.**
//     P03의 공백은 다르다 -- shared/p03-engine.js:128의 buildCombinedCodeContext()는 이미
//     finding.file로 최대 3개 파일까지 좁혀진 뒤, 4000자를 파일 수로 나눠 각 파일 앞에서부터
//     .slice(0, perFileCap)한다. 관련 코드가 그 지점보다 뒤에 있으면 조용히 사라진다.
//     여기서 부족한 건 "무엇이 중요한가"(1단계)도 "몇 개를 고를까"(3단계)도 아니다 -- 후보는
//     이미 3개로 좁혀져 있고 순위도 finding이 이미 갖고 있다. 부족한 건 **파일 안 어디를
//     보여줄까**뿐이고, 그건 정확히 2단계(심볼 grounding)가 푸는 문제다.
//     4단계는 P03에 특히 부적절하다: P03은 사람이 답을 타이핑하는 동안 동시 호출 1건으로
//     굴러가는 루프고(D181이 명시), 인터뷰 중 병렬 버스트를 넣으면 얻는 것 없이 429 위험만
//     늘린다.
//     -> **미해결 갈림길이라 여기서 코드를 넣지 않았다: P03의 심볼을 누가 만드는가?**
//        (A) finding 텍스트에 이미 들어있는 matched_text를 쓴다 -- 추가 비용 0. 단
//            tier-b-risk 계열에만 있고(score_findings.py:399/437), cognition-isolation /
//            architecture-diffusion / repeated-pattern에는 코드 조각이 아예 없다. 즉 절반은
//            여전히 오늘의 slice로 남는다. AUTH_KEYWORDS 계열 matched_text는 3글자짜리라
//            MIN_SYMBOL_CHARS에 걸려 실질적으로 못 쓴다.
//        (B) 심볼 선택용 LLM 호출을 인터뷰 시작 전에 1회 추가한다 -- 전 카테고리 커버되지만
//            P03 세션 시작 지연이 늘고(관측 지연 2~92s) 호출이 1회 는다.
//        (C) P02의 트리거 정규식을 브라우저에서 다시 돌려 해당 줄을 찾는다 -- 결정론적이고
//            전 카테고리 커버 가능하지만, 정규식이 Python 쪽에 있어 JS 재구현 = 드리프트 소스.
//        셋 다 나름의 근거가 있고, 어느 쪽이든 P03 사용자 경험을 실제로 바꾼다. 임의로 고르지
//        않고 저장소 소유자 판단으로 남긴다.
//     -> 공유 방식(정한 것): 채택되면 locateSymbol을 **shared/** 로 뽑는다(예:
//        shared/code-locate.js). .github/workflows/pages.yml의 drift-check가 shared/를
//        두 브랜치 간 바이트 동일로 강제하므로, 공유 표면은 그 폴더가 유일하게 안전한 자리다.
//        지금 미리 옮기지 않는 이유: 위 갈림길이 미해결이라 P03이 실제로 쓸지 확정되지 않았고,
//        쓰지도 않을 파일을 drift-check 대상에 올리면 두 브랜치에 유지보수 부채만 생긴다.
//
// ── buildCodeBlock()은 어떻게 되는가 -- 대체하지 않고 "정렬만 바꾼다" ─────────────
//   buildCodeBlock은 p04-1 **이전**에 돈다. 그 시점에 LLM 기반 중요도는 아직 없다 -- 그러니
//   "일단 들어가는 만큼 다 보여줘"라는 역할 자체는 여전히 정당하고, 부트스트랩/폴백으로 남긴다.
//   다만 **구조 신호(fan_in)는 p04-1 이전에 이미 존재한다**(P02Engine.run()이 먼저 끝난다).
//   그러므로 "중요도 신호가 없다"는 전제는 절반만 참이고, 알파벳순은 그냥 낭비다.
//   -> orderFilesByImportance()가 fan_in 순서를 만들고, buildCodeBlock에 opts.order로 넘길 수
//      있게 했다(기본값은 오늘과 동일한 알파벳순 -- 호출부를 안 바꾸면 동작이 한 비트도 안 변한다).
//   -> **stop이 아니라 skip은 유지한다.** 랭크 순으로 채우다 예산을 넘기는 파일을 만나면 거기서
//      멈추는 구현(Team-IZ-AI-codemap의 app/engines/codemap/shortlist.py)은 1등 파일 하나가
//      max_chars보다 크면 첫 반복에서 stopped=True가 되고 그 뒤 전부 truncated로 흘러
//      **selected가 통째로 빈다** -- 뒤쪽 작은 파일들은 충분히 들어갔을 텐데도. 그 함정을
//      복제하지 않는다(codemap은 별도 엔진이라 여기서 고치지 않는다). P04는 이미 skip이라
//      바꿀 게 없고, 바꾸지 않는다는 것 자체가 결정이다.
//   COST: fan_in은 basename 키라 동명 파일이 합산된다(위 1단계 COST와 같은 한계). 정렬 순서만
//      바뀌고 포함 여부 판정 로직은 그대로라, 최악이라도 "오늘과 다른 순서로 같은 예산을 채움"이다.
//   EXIT: fan_in 순서가 알파벳보다 낫다는 게 실측되면 poc-engine.js:81의 호출부에 order를
//      넘기는 한 줄만 바꾸면 된다. 나빠지면 그 한 줄을 되돌린다.
//
// ── 이 파일의 현재 상태 ────────────────────────────────────────────────────────────
//   구현됨(순수 로직, LLM 호출 0, 어디에도 배선 안 됨): collectCandidates / groundCandidates /
//     rankCandidates / selectTopK / orderFilesByImportance / resolveFanoutPlan.
//   설계만: 4단계의 실제 호출부(p04-1b 프롬프트, poc-engine.js 배선, analysis.html 렌더).
//     resolveFanoutPlan()은 "몇 개를 몇 병렬로 쏠지"를 계산만 하고 아무것도 호출하지 않는다 --
//     fan-out에서 가장 비싸고 되돌리기 어려운 건 호출 그 자체이므로, 승인 전까지는 예산 계산까지만 둔다.
const CodeCandidates = (() => {
  // ── 3단계 가중치 ─────────────────────────────────────────────────────────────
  // 전부 1.0에서 시작하는 unmeasured/provisional 값이다. judgment/rank_weights/
  // rank_weights.json의 provenance 필드와 같은 정직성 규약을 코드 안에서 지킨다.
  // 재보정 조건은 파일 헤더 3단계의 EXIT 참조 -- 측정 전에 감으로 고치지 말 것.
  const RANK_WEIGHTS = {
    llm_proposed: 1.0,
    finding_rank: 1.0,
    fan_in: 1.0,
    ground: 1.0,
    teach_linked: 1.0,
    agreement: 1.0,
  };
  const RANK_WEIGHTS_PROVENANCE =
    "provisional-equal (D-poc13) -- 실제 제출물에 대한 Precision@3 측정 전. 데이터 없이 조정 금지.";

  // 2단계 confidence 등급 -- 파일 헤더 2단계 COST 참조.
  const GROUND_CONFIDENCE = { exactUnique: 1.0, exactAmbiguous: 0.6, normalized: 0.5 };
  // 이보다 짧은 심볼은 매치 개수를 세도 신뢰할 수 없다.
  // D-ground1m 참고(2026-07-31): feat/code_Q&A의 shared/code-locate.js가 **같은 성격의**
  // 상수(MIN_SYMBOL_LEN)를 실측으로 8 -> 9로 옮겼다. 코퍼스는 이 브랜치 파일들도 포함한
  // 62파일/14,855줄이고, 결과는 심각 모호(같은 파일 5줄 이상 매치)가 L=4..8의 13.3%에서
  // L=9..13의 3.7%로 3.6배 꺾인다는 것 -- 즉 8은 그 꺾임 **직전**에 있다.
  //   여기 8을 아직 9로 바꾸지 않은 이유: 그쪽은 "짧으면 하드 리젝"이지만 이 상수는
  //   grounding confidence 강등(GROUND_CONFIDENCE/AMBIGUOUS_AT)과 맞물려 P04의 살아있는
  //   후보 선정 동작을 바꾼다. 측정은 그대로 적용되지만 **영향 범위가 달라서** 같은 변경으로
  //   묶지 않았다 -- 저장소 소유자 판단 대기 중인 열린 결정이다(보고서에 명시).
  //   바꾸게 되면 D-ground1m 블록의 수치를 여기에도 인용할 것. 감으로 바꾸지 말 것.
  const MIN_SYMBOL_CHARS = 8;
  const AMBIGUOUS_AT = 2; // 같은 파일에서 이 개수 이상 매치되면 "어느 줄인지 불확실"

  // ── 4단계 예산 상수 (설계값, 호출 없음) ──────────────────────────────────────
  const FANOUT_MAX_K = 5;
  const FANOUT_CONCURRENCY = 2;
  const WORST_ATTEMPTS_PER_CALL = 3; // worker/nvidia-proxy.js MAX_ATTEMPTS -- 브라우저에 안 보이는 재시도
  const RATE_THRESHOLD_FALLBACK = 40; // shared/traffic-rate.js RATE_LIMIT_THRESHOLD와 같은 값
  const TAB_ONLY_DISCOUNT = 0.5; // isServerWide=false면 다른 팀원 트래픽이 안 잡히므로 여유를 깎는다

  function splitLines(content) {
    return String(content).split(/\r\n|\r|\n/);
  }
  function normalizeForMatch(s) {
    return String(s).replace(/\s+/g, " ").trim();
  }
  function basenameOf(path) {
    return String(path || "").split("/").pop();
  }

  // ── 1단계: 후보 수집 ─────────────────────────────────────────────────────────
  // 세 소스를 하나의 후보 형태로 모은다: {source, file, symbol, title, why, meta}
  // 새 LLM 호출 없음 -- 전부 이미 계산된 값을 읽기만 한다(파일 헤더 1단계 WHY 참조).

  // P02 finding 텍스트에 박혀 있는 matched_text=... 를 뽑는다(score_findings.py가 파이썬
  // !r로 찍어 넣은 것이라 따옴표가 붙어 있다). 없으면 null -- 그 finding은 파일 단위 후보가 된다.
  const MATCHED_TEXT_RE = /matched_text=(['"])([\s\S]*?)\1/;
  function symbolFromFinding(finding) {
    const m = MATCHED_TEXT_RE.exec(String(finding.finding || ""));
    return m ? m[2] : null;
  }

  // 구조 후보의 심볼: 파일 안에서 "정의처럼 보이는" 첫 줄. 파일에서 그대로 떼어온 문자열이라
  // grounding이 항상 성공한다 -- 구조 후보에게 grounding은 사실상 no-op이고, 그건 의도된 것이다
  // (grounding의 존재 이유는 LLM이 지어낸 위치를 거르는 것이지, 우리가 읽은 줄을 검증하는 게 아니다).
  const DEFINITION_RES = [
    /^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn)\s+\w+/,
    /^\s*(?:public|private|protected|internal)\s+[\w<>\[\],\s]+\(/,
    /^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\(|function)/,
    /^\s*(?:export\s+)?(?:interface|struct|impl|enum|type)\s+\w+/,
  ];
  function symbolFromStructure(content) {
    const lines = splitLines(content);
    for (const line of lines) {
      if (line.trim().length < MIN_SYMBOL_CHARS) continue;
      if (DEFINITION_RES.some((re) => re.test(line))) return line.trim();
    }
    return null;
  }

  /**
   * @param {object} input
   *   analysisDoc  p04-1 결과 (decision_points를 읽는다). 없으면 llm 소스 생략.
   *   findings     P02 judgment.findings (rank_score/rank 포함 -- D194가 이미 붙여둠)
   *   fanIn        p02Result.scan.tier_a_structural.fan_in ({basename: count}) -- 선택
   *   files        {path: content}
   *   teachIds     이번에 검증할 teach id Set/배열 -- 선택(teach_linked 항 계산용)
   *   structuralTop  구조 후보를 상위 몇 개까지 만들지 (기본 5)
   * @returns {Array} 후보 배열 (아직 grounding/랭킹 전)
   */
  function collectCandidates({ analysisDoc, findings, fanIn, files, teachIds, structuralTop = 5 } = {}) {
    const out = [];
    const teachSet = teachIds instanceof Set ? teachIds : new Set(teachIds || []);

    for (const dp of (analysisDoc && Array.isArray(analysisDoc.decision_points) ? analysisDoc.decision_points : [])) {
      out.push({
        source: "llm",
        file: dp.file,
        symbol: dp.symbol || null,
        title: dp.title || "",
        why: dp.why_it_matters || "",
        meta: { related_teach: dp.related_teach || null, teach_linked: !!(dp.related_teach && teachSet.has(dp.related_teach)) },
      });
    }

    for (const f of (Array.isArray(findings) ? findings : [])) {
      if (!f.file) continue; // repeated-pattern처럼 단일 파일에 귀속되지 않는 finding (D76)
      out.push({
        source: "finding",
        file: f.file,
        symbol: symbolFromFinding(f),
        title: f.id || "",
        why: f.finding || "",
        meta: { finding_rank_score: Number(f.rank_score) || 0, priority: f.priority || null },
      });
    }

    if (fanIn && files) {
      const ranked = Object.keys(fanIn)
        .map((name) => ({ name, count: Number(fanIn[name]) || 0 }))
        .sort((a, b) => (b.count - a.count) || a.name.localeCompare(b.name))
        .slice(0, structuralTop);
      for (const { name, count } of ranked) {
        const path = Object.keys(files).find((p) => basenameOf(p) === name);
        if (!path) continue;
        out.push({
          source: "structural",
          file: path,
          symbol: symbolFromStructure(files[path]),
          title: `${name} (fan_in=${count})`,
          why: `구조 스캔 기준 참조가 많은 파일 (fan_in=${count})`,
          meta: { fan_in: count },
        });
      }
    }

    return out;
  }

  // ── 2단계: grounding ─────────────────────────────────────────────────────────
  // 심볼이 파일에서 몇 줄에 매치되는지 우리가 직접 센다 -- locateSymbol은 첫 매치만 주므로
  // "유일한가"를 알 수 없다(파일 헤더 2단계 COST 참조).
  function countSymbolMatches(content, symbol) {
    const needle = String(symbol || "").trim();
    if (!needle) return { exact: 0, normalized: 0 };
    const lines = splitLines(content);
    const exact = lines.filter((l) => l.includes(needle)).length;
    const normNeedle = normalizeForMatch(needle);
    const normalized = lines.filter((l) => normalizeForMatch(l).includes(normNeedle)).length;
    return { exact, normalized };
  }

  /**
   * 후보마다 실제 소스와 대조해 위치를 확정한다. 위치 산정은 CodeFragment.locateSymbol에
   * 전적으로 위임한다(복제 금지 -- 파일 헤더 2단계 WHY).
   * @param {object} files
   * @param {Array} candidates
   * @param {object} [opts.locate]  locateSymbol 주입구(테스트/재사용용). 기본은 전역 CodeFragment.
   * @returns {Array} 후보 + {grounded:boolean, located|null, confidence, groundReason}
   */
  function groundCandidates(files, candidates, opts = {}) {
    const locate = opts.locate
      || (typeof CodeFragment !== "undefined" ? CodeFragment.locateSymbol : null);
    if (!locate) throw new Error("groundCandidates: locateSymbol을 찾을 수 없음 (code-fragment.js 로드 필요)");

    return (candidates || []).map((c) => {
      const path = Object.prototype.hasOwnProperty.call(files, c.file)
        ? c.file
        : Object.keys(files).find((p) => basenameOf(p) === basenameOf(c.file));
      if (!path) {
        return { ...c, grounded: false, located: null, confidence: 0, groundReason: `파일을 찾을 수 없음: ${c.file}` };
      }

      const symbol = String(c.symbol || "").trim();
      if (symbol.length < MIN_SYMBOL_CHARS) {
        // 파일은 실재하지만 가리킬 줄이 없다 -- 버리지 않고 "파일 단위 후보"로 남긴다.
        // (오늘의 P02 finding이 정확히 이 상태다. 이걸 버리면 정보가 오히려 줄어든다.)
        return {
          ...c, grounded: false, located: null, confidence: 0, fileResolved: path,
          groundReason: symbol ? `심볼이 너무 짧아 위치 특정 불가(${symbol.length}<${MIN_SYMBOL_CHARS})` : "심볼 없음(파일 단위 후보)",
        };
      }

      const located = locate(files, path, symbol);
      if (!located.valid) {
        // 지어낸 위치 -- D-poc6/D-poc10 규율 그대로 버린다.
        return { ...c, grounded: false, located: null, confidence: 0, fileResolved: path, groundReason: located.reason };
      }

      const counts = countSymbolMatches(files[path], symbol);
      let confidence = GROUND_CONFIDENCE.exactUnique;
      let note = "정확 매치(유일)";
      if (counts.exact === 0) {
        confidence = GROUND_CONFIDENCE.normalized;
        note = "공백 정규화 후 매치";
      } else if (counts.exact >= AMBIGUOUS_AT) {
        confidence = GROUND_CONFIDENCE.exactAmbiguous;
        note = `정확 매치 ${counts.exact}곳 -- 첫 번째 줄로 확정됨(다른 줄일 수 있음)`;
      }
      return { ...c, grounded: true, located, confidence, fileResolved: path, groundReason: note, matchCounts: counts };
    });
  }

  // ── 3단계: 랭킹 ──────────────────────────────────────────────────────────────
  // D194(judgment/importance_rank.py)의 형태를 그대로 따른다: 가중합 -> rank_evidence 부착 ->
  // 완전순서 타이브레이크 -> tie_break_depth 기록.

  // 같은 위치를 여러 소스가 지목하면 하나로 합친다. "두 소스가 독립적으로 같은 곳을 찍었다"는
  // 것 자체가 신호이므로(agreement 항) 여기서 소스 목록을 보존한다.
  function mergeByLocation(grounded) {
    const byKey = new Map();
    for (const c of grounded) {
      const line = c.located ? c.located.matchedLine : 0;
      const key = `${c.fileResolved || c.file}#${line}`;
      const prev = byKey.get(key);
      if (!prev) {
        byKey.set(key, { ...c, sources: [c.source] });
        continue;
      }
      // grounding이 더 강한 쪽(confidence 높은 쪽)을 대표로 남기고 나머지 메타는 병합한다.
      const winner = c.confidence > prev.confidence ? { ...c } : { ...prev };
      winner.sources = Array.from(new Set([...(prev.sources || []), c.source]));
      winner.meta = { ...(prev.meta || {}), ...(c.meta || {}) };
      byKey.set(key, winner);
    }
    return Array.from(byKey.values());
  }

  function sortKey(c) {
    // 오름차순 정렬 시 중요한 게 앞에 오도록 전부 음수/역순. 마지막은 항상 문자열이라
    // 동점이 끝까지 남지 않는다(D194의 재현성 보장과 같은 규약).
    return [
      -c.rank_score,
      -(c.confidence || 0),
      -(c.rank_evidence.terms.fan_in || 0),
      -(c.rank_evidence.terms.finding_rank || 0),
      `${c.fileResolved || c.file}#${c.located ? c.located.matchedLine : 0}`,
    ];
  }

  /**
   * @param {Array} grounded  groundCandidates()의 결과
   * @param {object} opts.fanIn  {basename: count} -- 선택
   * @param {object} opts.weights  RANK_WEIGHTS 오버라이드 -- 선택
   * @returns {Array} rank/rank_score/rank_evidence가 붙고 정렬된 후보
   */
  function rankCandidates(grounded, opts = {}) {
    const weights = { ...RANK_WEIGHTS, ...(opts.weights || {}) };
    const fanIn = opts.fanIn || {};
    const merged = mergeByLocation(grounded || []);

    const maxFanIn = Math.max(0, ...Object.values(fanIn).map((v) => Number(v) || 0));
    const maxFindingRank = Math.max(
      0,
      ...merged.map((c) => Number(c.meta && c.meta.finding_rank_score) || 0)
    );
    const weightSum = Object.values(weights).reduce((a, b) => a + b, 0);

    const scored = merged.map((c) => {
      const terms = {
        llm_proposed: (c.sources || []).includes("llm") ? 1 : 0,
        finding_rank: maxFindingRank > 0 ? (Number(c.meta && c.meta.finding_rank_score) || 0) / maxFindingRank : 0,
        fan_in: maxFanIn > 0 ? (Number(fanIn[basenameOf(c.fileResolved || c.file)]) || 0) / maxFanIn : 0,
        ground: c.confidence || 0,
        teach_linked: c.meta && c.meta.teach_linked ? 1 : 0,
        agreement: (c.sources || []).length >= 2 ? 1 : 0,
      };
      const weighted = Object.keys(terms).reduce((sum, k) => sum + (weights[k] || 0) * terms[k], 0);
      const rank_score = weightSum > 0 ? Number((weighted / weightSum).toFixed(6)) : 0;
      return {
        ...c,
        rank_score,
        rank_evidence: { weights, weights_provenance: RANK_WEIGHTS_PROVENANCE, terms, tie_break_depth: null },
      };
    });

    scored.sort((a, b) => {
      const ka = sortKey(a), kb = sortKey(b);
      for (let i = 0; i < ka.length; i++) {
        if (ka[i] < kb[i]) return -1;
        if (ka[i] > kb[i]) return 1;
      }
      return 0;
    });

    scored.forEach((c, i) => {
      c.rank = i + 1;
      if (i === 0) { c.rank_evidence.tie_break_depth = 0; return; }
      const ka = sortKey(scored[i - 1]), kb = sortKey(c);
      let depth = ka.length;
      for (let d = 0; d < ka.length; d++) { if (ka[d] !== kb[d]) { depth = d; break; } }
      c.rank_evidence.tie_break_depth = depth;
    });

    return scored;
  }

  /**
   * 심층 분석에 태울 상위 K개. grounded(=가리킬 줄이 확정된) 후보만 대상이다 -- 파일 단위
   * 후보는 "이 위치 하나만 파고들라"는 프롬프트를 만들 수 없으므로 fan-out 대상이 아니다
   * (분석 문서에는 계속 남는다. 여기서 빠지는 것과 버려지는 것은 다르다).
   * 한 파일에 후보가 몰리는 것을 막기 위해 파일당 상한을 둔다.
   */
  function selectTopK(ranked, { k = 3, maxPerFile = 2 } = {}) {
    const perFile = new Map();
    const picked = [];
    for (const c of ranked || []) {
      if (picked.length >= k) break;
      if (!c.grounded) continue;
      const file = c.fileResolved || c.file;
      const used = perFile.get(file) || 0;
      // skip이지 stop이 아니다 -- 상위 후보 하나가 조건에 안 맞아도 그 아래를 전부 버리지
      // 않는다(codemap shortlist.py의 stopped=True 캐스케이드 함정. 파일 헤더 buildCodeBlock 절 참조).
      if (used >= maxPerFile) continue;
      perFile.set(file, used + 1);
      picked.push(c);
    }
    return picked;
  }

  // ── buildCodeBlock 보조: 중요도(fan_in) 순 파일 정렬 ────────────────────────────
  // buildCodeBlock(files, {order: orderFilesByImportance(files, fanIn)})로 넘기면 알파벳순
  // 대신 이 순서로 예산을 채운다. 넘기지 않으면 오늘 동작 그대로(기본값 알파벳순).
  function orderFilesByImportance(files, fanIn) {
    const fi = fanIn || {};
    return Object.keys(files || {}).sort((a, b) => {
      const fa = Number(fi[basenameOf(a)]) || 0;
      const fb = Number(fi[basenameOf(b)]) || 0;
      if (fb !== fa) return fb - fa;
      return a.localeCompare(b); // 동점은 알파벳 -- 완전순서 유지(재현성)
    });
  }

  // ── 4단계 예산 계산 (설계값. 아무것도 호출하지 않는다) ─────────────────────────
  /**
   * 현재 공유 트래픽을 보고 "몇 개를 몇 병렬로 쏠지"만 계산한다.
   * @param {object} rate  DebugTraffic.getCurrentRate()의 반환 형태 {count,isServerWide,threshold}
   * @param {object} opts  {k}  희망 K
   * @returns {{k:number, concurrency:number, maxAttempts:undefined, headroom:number, reason:string}}
   */
  function resolveFanoutPlan(rate, opts = {}) {
    const wantK = Math.min(Number(opts.k) || 3, FANOUT_MAX_K);
    if (!rate || typeof rate.count !== "number") {
      // 트래픽을 못 읽는 상황(프록시 미설정 등)에서는 낙관하지 않는다 -- 최소 병렬로만 간다.
      return { k: Math.min(wantK, FANOUT_CONCURRENCY), concurrency: 1, maxAttempts: undefined, headroom: NaN,
        reason: "트래픽을 확인할 수 없어 보수적으로 동시성 1" };
    }
    const threshold = Number(rate.threshold) || RATE_THRESHOLD_FALLBACK;
    let headroom = Math.max(0, threshold - rate.count);
    if (!rate.isServerWide) headroom = Math.floor(headroom * TAB_ONLY_DISCOUNT);
    // 제출 1건 = 최대 WORST_ATTEMPTS_PER_CALL회의 실제 NVIDIA 요청(브라우저에 안 보임)
    const safeSubmissions = Math.floor(headroom / WORST_ATTEMPTS_PER_CALL);
    const k = Math.max(0, Math.min(wantK, safeSubmissions));
    return {
      k,
      concurrency: Math.min(FANOUT_CONCURRENCY, Math.max(1, k)),
      // 일부러 워커 기본값(3) 유지 -- P03 D181과 반대로, 부하가 높으면 재시도를 사지 말고
      // K를 깎는다(파일 헤더 4단계 설계 규칙 3).
      maxAttempts: undefined,
      headroom,
      reason: k === 0
        ? `현재 트래픽 ${rate.count}/${threshold}로 여유 없음 -- 심층 분석 생략(오늘 동작으로 degrade)`
        : `여유 ${headroom}rpm -> 최대 제출 ${safeSubmissions}건, K=${k} 동시성 ${Math.min(FANOUT_CONCURRENCY, Math.max(1, k))}`,
    };
  }

  return {
    collectCandidates, groundCandidates, rankCandidates, selectTopK,
    orderFilesByImportance, resolveFanoutPlan,
    countSymbolMatches, symbolFromFinding, symbolFromStructure,
    RANK_WEIGHTS, RANK_WEIGHTS_PROVENANCE, GROUND_CONFIDENCE,
    MIN_SYMBOL_CHARS, FANOUT_MAX_K, FANOUT_CONCURRENCY,
  };
})();

// 브라우저에서는 위 전역 상수 하나로 끝이고, 이 줄은 node --test에서 순수 로직을 검증하기
// 위한 것이다(app/code-candidates.test.js). 브라우저에는 module이 없으므로 no-op.
if (typeof module !== "undefined" && module.exports) module.exports = CodeCandidates;
