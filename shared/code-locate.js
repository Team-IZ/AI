// LLM/스캐너가 지목한 {file, symbol} 참조를 **실제 제출 소스와 대조해** 코드 위치를 산정한다.
// 존재하지 않는 코드는 절대 통과시키지 않는다.
//
// ============================================================================
// D-ground1 (2026-07-31) -- 설계 문서 겸 구현. Tier B 폐기(D-tierb1) 이후 P02→P03에
// 생긴 "하향식 중요도 신호" 공백을 무엇으로 메울지, 그리고 그 신호를 어떻게 신뢰
// 가능한 코드 근거로 바꿀지에 대한 결정 기록.
// ============================================================================
//
// ## 0. 이 파일이 존재하는 이유 (무엇이 비었는가)
//
// P02는 이제 Tier A(구조 스캔)만 남았다. Tier A는 "어느 **파일**이 중요한가"(fan_in,
// hub/isolation/diffusion)까지만 안다 -- **파일 안 어디인지는 모른다**. 반면 P03이
// 학생에게 보여줄 근거는 파일 전체가 아니라 코드 조각이어야 한다.
// 그 간극이 shared/p03-engine.js::buildCombinedCodeContext()의 알려진 결함이다:
// 파일 **앞에서부터** perFileCap자를 자르므로 정작 논점이 되는 코드가 뒤에 있으면
// 프롬프트에서도 학생 화면에서도 조용히 사라진다.
//
// feat/poc_full의 P04는 이 문제를 이미 풀었다(app/code-fragment.js::locateSymbol,
// 그쪽 D-poc10). 이 파일은 그 해법을 code_Q&A로 가져오되, 아래 1~2절의 두 가지를
// 이 저장소 사정에 맞게 바꾼 것이다.
//
// ## 1. 왜 JS인가 (어느 언어 계층에 두는가)
//
// P02/judgment는 Python(Pyodide)인데 이 파일은 JS다. 확인한 근거:
//   - P03의 LLM 호출은 전부 브라우저에서 shared/llm.js(LabLLM.chatJSON/chatTool/
//     chatToolLoop)를 통해 Cloudflare Worker 프록시로 나간다. 서버 사이드 추론 경로가
//     아예 없다(worker/nvidia-proxy.js는 중계만 한다).
//   - 새 분석 문서 단계도 같은 경로를 쓸 수밖에 없으므로, 그 응답을 받아 검증하는 지점도
//     브라우저다. 여기서 Python을 쓰려면 grounding만을 위해 Pyodide를 깨워야 하는데
//     (P03은 이미 분류기용으로 띄우긴 하지만) LLM 응답 검증을 Python으로 왕복시킬 이유가 없다.
//   WHY 별도 파일: P02Engine/P03Engine 어느 쪽 IIFE에도 넣지 않았다 -- 이 로직의 소비자는
//   양쪽 다이고(P02 이후 단계가 심볼을 만들고, P03이 그 위치로 코드를 자른다), 무엇보다
//   node --test로 브라우저 없이 검증 가능해야 한다.
//   COST: Python 파이프라인과 JS grounding으로 언어가 갈린다. 단, grounding은 정규식/파서가
//   아니라 **문자열 포함 검사**라 언어별 드리프트 위험이 실질적으로 없다(아래 2절).
//   EXIT: 서버 사이드 배치 채점이 생기면 같은 규칙을 Python으로 재구현해야 한다 -- 그때는
//   이 파일의 테스트(tests/code-locate.test.js)를 명세로 삼아 동등성을 확인할 것.
//
// ## 2. 왜 "LLM이 센 줄 번호"가 아니라 "LLM이 인용한 한 줄"인가
//
// poc_full D-poc10의 실사용 교훈을 그대로 승계한다: 처음엔 LLM에게 [시작,끝] 줄 번호를
// 세게 했는데 긴 파일에서 자주 틀렸고, 틀린 건 버릴 수밖에 없어 "코드 조각을 확인할 수
// 없음"만 반복됐다. 근본 원인은 "LLM이 센다"는 설계였다.
//   결론: LLM은 **복사**만 한다(실제 코드 한 줄을 그대로 인용). 그 문자열이 몇 번째 줄인지는
//   우리가 직접 찾는다. "산정된 사실"과 "LLM의 주장"을 분리한다.
//   COST: LLM이 코드를 요약·재구성해 인용하면(여러 줄을 한 줄로 합치는 등) 못 찾는다 --
//   그때는 버린다(6절). 블록 끝 추정은 들여쓰기 휴리스틱이라 완벽하지 않다.
//   EXIT: 블록 끝 정확도가 문제가 되면 estimateBlockEnd()만 언어별 파서로 교체하면 된다 --
//   심볼 매칭 자체는 그대로 재사용 가능.
//
// ## 3. poc_full의 locateSymbol을 그대로 베끼지 않은 부분 (실측 기반 하드닝 2건)
//
// poc_full은 locateSymbol(raw)과 그 위의 방어 로직(code-candidates.js)이 **분리**돼 있어,
// locateSymbol만 단독으로 쓰면 아래 두 실패에 그대로 노출된다. 이 저장소는 소비자가
// 하나뿐이므로 처음부터 한 함수 안에 넣었다:
//   (a) 너무 짧은 심볼 -> 위치로 인정하지 않음(MIN_SYMBOL_LEN).
//       근거: 폐기된 Tier B의 matched_text가 "uid"처럼 3글자였다(feat/poc_full
//       app/code-candidates.test.js:72-82이 회귀로 박아둔 실제 사례). 3글자는 파일 어디에나
//       있어서 첫 매치가 엉뚱한 줄일 확률이 높은데, 실패가 아니라 **조용한 오답**으로 나온다.
//   (b) 여러 줄에 매치되는 심볼 -> 버리지는 않되 ambiguous로 강등하고 매치 수를 함께 반환.
//       근거: `    print(msg)` 같은 흔한 한 줄은 정당하게 여러 번 나온다. 버리면 근거가
//       과하게 사라지고, 조용히 첫 줄을 쓰면 틀린 근거를 확신에 차서 보여주게 된다.
//       -> 호출부가 "이 위치는 여러 후보 중 하나"라고 **정직하게 표시**할 수 있게 정보를 넘긴다.
//   측정(D-ground1m, 2026-07-31 -- 임계값 8은 손으로 고른 값이었고, 아래로 대체한다):
//     코퍼스: 이 저장소 두 브랜치(feat/code_Q&A, feat/poc_full)의 실제 소스 62개 파일,
//       비어있지 않은 줄 14,855개 -- cognition/ judgment/ shared/ app/ worker/ feedback/
//       reference/ tests/ + webtool_driver.py의 .py/.js/.jsx/.ts/.tsx/.java 전부.
//       합성 코드는 쓰지 않았다.
//     방법: locateSymbol의 동작을 그대로 재현한다 -- 각 파일의 **실제 트림된 줄**을 심볼로
//       삼아(LLM이 "실제로 있는 한 줄을 그대로 옮겨 적어라"를 지킨 경우가 정확히 이것),
//       같은 파일에서 lines[i].includes(needle)로 매치되는 줄 수를 센다. 길이 L별 집계.
//     결과(distinct 줄 기준, matchCount 분포):
//       L=1..3   n=  268  mean=41.31  >=2match=95.5%  >=5match=82.5%   <- 재앙 구간
//       L=4..8   n=  255  mean= 2.53  >=2match=48.2%  >=5match=13.3%
//       L=9..13  n=  485  mean= 1.46  >=2match=21.0%  >=5match= 3.7%   <- 여기서 3.6x 꺾임
//       L=14..20 n=  726  mean= 1.34  >=2match=16.1%  >=5match= 1.5%
//       L=21..40 n= 2680  mean= 1.14  >=2match= 6.8%  >=5match= 0.8%
//     -> **9로 정한다.** 근거는 두 개의 실측 불연속이다: (1) L<=3은 중앙값 매치수가 6~46인
//       사실상 무작위 구간이고("uid"가 여기 있었다), (2) 심각 모호(>=5match)가 L=4..8의
//       13.3%에서 L=9..13의 3.7%로 3.6배 떨어진 뒤 다시 올라오지 않는다. 8을 그대로 두면
//       L=8 버킷(n=96, >=2match 44.8%, >=5match 13.5%)이 통과 집단에 남는데, 이 버킷은
//       `} else {` 같은 관용구 한 줄들이라 첫 매치가 엉뚱할 확률이 실제로 높다.
//       8->9의 이득은 크지 않다(통과집단의 >=5match 0.51%->0.41%). 그래도 9를 고르는 건
//       비용이 거의 없기 때문이다: 9 미만이면서 유일한 줄은 코퍼스의 1.69%뿐이다.
//   COST: (1) 길이는 L>=4 이후로는 **약한 예측자**다 -- 임계값을 24까지 올려도 통과집단의
//   모호율은 4.45%에서 멈춘다(대신 코퍼스의 17.4%를 버리게 된다). 즉 이 상수는 재앙 구간을
//   자르는 장치일 뿐이고, 실질적인 방어는 §3(b)의 ambiguous/matchCount 표시가 한다 --
//   이 상수를 더 올려서 정확도를 사려는 시도는 측정상 무의미하다.
//   (2) 코퍼스가 학생 제출물이 아니라 **이 저장소의 인프라 코드**다(주석 비중이 높고 한 줄이
//   긴 편이며, 숙련자가 쓴 성숙한 코드다). 학생 코드는 더 짧고 반복적일 가능성이 커서 같은
//   길이에서 모호율이 더 높을 수 있다 -- 그러면 9는 하한이지 상한이 아니다.
//   (3) 짧지만 유일한 심볼(예: `}) ;`)은 여전히 버려진다(위 1.69%).
//   재보정 조건(EXIT): 아래 셋 중 하나라도 참이면 다시 측정한다.
//     (i) 실제 학생 제출물 코퍼스가 생겼을 때 -- 위 COST(2)가 미검증 가정이므로 최우선.
//     (ii) 대상 언어가 바뀔 때(예: 한 줄이 짧은 Go/Swift 위주로 이동).
//     (iii) 운영에서 "너무 짧아" 드랍률이 5%를 넘을 때 -- 그때는 임계값을 낮추는 대신
//          "짧아도 파일 내 유일하면 통과"로 완화한다(이미 matchCount를 계산하므로 한 줄 변경).
//     재측정 스크립트의 방법은 위 "방법" 항목 그대로다(코퍼스 경로만 바꿔 재실행).
//     현재 값은 tests/code-locate.test.js가 회귀로 고정한다.
//
// ## 4. 새 단계의 설계 (아직 **배선하지 않음** -- 5절이 배선 방법)
//
// 이름/위치: **p02-6 "코드 분석 문서"**. 저장소 소유자가 2026-07-31에 확정했다 -- p03-0도,
// 별도 파이프라인도 아니다. 이 단계는 P02 우산 아래 산다. 번호가 6인 이유는 p02-3이
// Tier B의 결번으로 비어 있어도 **재사용하지 않기** 때문이다(D-tierb1의 비재번호 규율과
// 같은 근거: stage id는 runs.overrides/Supabase에 저장된 키다 -- 비어 있는 번호에 다른
// 의미를 넣으면 과거 레코드가 조용히 새 단계로 읽힌다). 매니페스트 반영 상태는 7절.
// 흐름상 위치: P02(Tier A 스캔 + 채점 + 랭킹) -> **p02-6** -> P03(면접 루프).
//   WHY P03이 아니라 P02 끝인가: 제출물 전체를 한 번 읽는 하향식 판단은 **세션당 1회**면
//   충분하고, P03은 사람이 답을 타이핑하는 동안 도는 루프라 여기에 호출을 더하면 매 턴
//   지연이 늘어난다. P04도 같은 이유로 분석 문서를 세션 시작 전 1회만 만든다.
//
// 프롬프트 모양(poc_full app/prompt_manifest.json의 p04-1을 이 저장소에 맞게 축소):
//   system: 학생 제출물을 구술 평가용으로 분석한다. 엄격한 JSON만 출력. 주어진 파일에
//           없는 파일/함수/동작을 지어내지 말 것.
//   user_template 자리표시자: {findings_block}, {code_block}
//     - {teaches_block}는 **뺀다** -- 교안(teaches) 개념은 feat/poc_full 전용이고
//       code_Q&A에는 없다. 그대로 옮기면 채울 수 없는 자리표시자가 된다.
//   출력 JSON: { "overview": str,
//                "decision_points": [{title, file, symbol, why_it_matters}] }
//     - P04의 structure[]/risks[]는 v1에서 뺀다: P03은 finding 하나를 골라 그 주제로
//       면접하는 도구라 소비자가 없다. 필요해지면 그때 추가한다(출력만 늘리면 됨).
//   규칙 문구(그대로 유지해야 하는 핵심): "symbol은 줄 번호를 세지 말고, 위 소스 코드에
//   실제로 있는 한 줄을 공백까지 그대로 옮겨 적어라. 줄 번호는 우리가 그 문자열을 찾아
//   직접 산정한다 -- 네가 지어낸 번호는 쓰지 않는다."
//
// **structural context를 얼마나 줄 것인가(요구된 판단)**: findings_block으로 **랭킹된
// finding 상위 N건의 {id, file, finding, rank_score}만** 넣는다. subrubric 전체는 넣지 않는다.
//   WHY: (1) Tier A의 hub/isolation/diffusion은 "이 파일이 구조적으로 중요하다"는 정보라
//   LLM이 스스로 세기 어려운 신호다 -- 넣으면 값이 있다. (2) 반대로 subrubric 원점수/버킷까지
//   넣으면 LLM이 우리 채점을 그대로 되읊는 쪽으로 끌려가(anchoring) "하향식 독립 신호"라는
//   존재 이유 자체가 사라진다. 두 신호가 독립이어야 나중에 둘이 **일치할 때** 그게 의미 있는
//   증거가 된다(poc_full이 agreement 항으로 쓰는 그 구조). (3) 12,000자 code_block 예산이
//   진짜 병목이라 finding JSON에 예산을 더 쓸 이유가 없다.
//   COST: LLM이 Tier A가 놓친 구조(예: Swift처럼 구조 스캔 자체가 불가한 언어)를 볼 때
//   힌트 없이 가야 한다. 그건 감수한다 -- 그 경우 hint가 애초에 존재하지 않는다.
//   EXIT: 실제로 decision_points가 finding과 거의 안 겹치면 findings_block을 늘리기 전에
//   **겹침률부터 측정**할 것. 겹침이 낮은 게 결함인지 하향식 신호의 가치인지 먼저 판정해야 한다.
//
// truncation / max_tokens (전부 poc_full p04-1의 실사용값 승계, unmeasured-provisional):
//   truncation: { code_block: 12000, findings_block: 6000 }
//   params: max_tokens 2400, temperature 0.0(locked -- 같은 제출물이 같은 분석을 내야
//           비교와 재현이 가능하다. D-ground1은 이 lock을 P04에서 그대로 가져온다)
//   code_block 채우는 순서: 알파벳순 금지. Tier A의 fan_in 내림차순으로 채운다
//   (fan_in은 이미 계산돼 있어 추가 비용 0). 알파벳으로 늦은 핵심 파일이 예산에서 잘리는
//   문제는 poc_full이 실측한 그대로다.
//
// ## 5. 배선하려면 무엇을 해야 하는가 (한 단계짜리 다음 작업)
//
// poc_full의 poc-engine.js:79-85가 unwired `order` 파라미터를 가리키는 것과 같은 방식으로,
// 여기서도 **배선 지점은 정확히 한 곳**이다:
//
//   trainee/submission.html이 P02Engine.run()의 결과를 받은 직후, P03으로 넘기기 전에:
//     const doc = await LabLLM.chatJSON({ model, maxTokens: 2400, temperature: 0,
//                   messages: [{role:"system",...},{role:"user", content: <4절 템플릿 채운 것>}] });
//     const points = (doc.decision_points || [])
//       .map(dp => ({ ...dp, located: CodeLocate.locateSymbol(files, dp.file, dp.symbol) }))
//       .filter(dp => dp.located.valid);   // <- 6절 규율: 못 찾으면 버린다
//
//   그리고 이 파일과 함께 필요한 것: (a) prompt_manifest.json에 p02-6 stage 추가,
//   (b) trainee/submission.html에 <script src="../shared/code-locate.js">,
//   (c) shared/p03-engine.js::buildCombinedCodeContext()가 앞에서부터 자르는 대신
//       located.lines 주변을 자르도록 변경.
//   지금 배선하지 않은 이유: (a)는 매니페스트 계약 변경이고(7절), 무엇보다
//   **실제 LLM 호출 비용/지연을 파이프라인에 추가하는 결정**이라 검토 없이 넣지 않는다.
//   이 파일의 순수 로직(아래)은 그 결정과 무관하게 지금 검증 가능하다.
//
// ## 6. 절대 타협하지 않는 규율 (poc_full D-poc6 승계)
//
// 심볼을 파일에서 못 찾으면 그 decision point는 **통째로 버린다**. 위치를 모르는 채로
// 파일 앞부분을 대신 보여주거나, LLM이 말한 줄 번호를 믿거나, "위치 미상"으로 표시하고
// 진행하지 않는다 -- 학생에게 자기 제출물에 없는 코드를 근거라고 보여주는 사고는
// 구조적으로 차단한다. 버린 개수는 호출부가 사용자에게 알린다.
//
// ## 7. 매니페스트 / 드리프트 (2026-07-31 해소됨)
//
//   - **브랜치 간 드리프트 -- 해소.** .github/workflows/pages.yml의 "Drift-check vendored
//     files"는 cognition/judgment/feedback/shared/worker/prompt_manifest.json/
//     webtool_driver.py를 feat/poc_full과 `diff -r`로 비교하고, 한쪽에만 있는 파일도
//     차이로 보고 exit 1을 낸다. 선택한 해법은 "drift-check 목록 조정"이 아니라
//     **두 브랜치를 함께 바꾸기**다: D-tierb1/D-tierb2와 이 파일이 feat/poc_full에도
//     **바이트 동일하게** 반영됐다. 반대로, poc_full 쪽에서만 성립하는 이야기(P04의
//     finding_rank 항이 Tier B 제거로 어떻게 퇴화하는가)는 벤더링된 judgment/에 적으면
//     그 자체가 드리프트가 되므로, poc_full 고유 파일인 app/code-candidates.js의
//     D-tierb3에 적었다 -- 벤더링 표면에는 양쪽 공통 사실만 남긴다.
//     이 파일은 poc_full에서 **아직 아무도 쓰지 않는다** -- P04는 자기 것인
//     app/code-fragment.js(D-poc10)를 계속 쓴다. 벤더링된 표면을 바이트 동일하게 유지하는
//     이 저장소의 "한 벌만 두고 동기화" 관례를 지키기 위한 사본이다.
//     COST: shared/를 건드릴 때마다 두 워크트리를 함께 고쳐야 한다(drift-check가 그걸
//     강제한다). 의도적으로 갈라둔 예외는 worker/wrangler.toml과 shared/config.js뿐이다.
//
//   - **`has_llm_calls`는 `false`로 **유지**한다 (p02-6이 실제로 배선될 때까지).**
//     WHY: 이 플래그는 "설계상 무엇이 있는가"가 아니라 **런타임에 실제로 무엇이 호출되는가**를
//     가리킨다. 지금 P02를 돌리면 LLM 호출은 0건이고, 지금 true로 바꾸면 배너
//     ("이 파이프라인은 LLM을 호출하지 않습니다")와 "키 없이 도는 무료 단계"라는 성질이
//     **반대 방향으로 거짓**이 된다 -- 트레이니에게 필요 없는 API 키를 요구하는 셈이다.
//     지금의 false는 부정확한 게 아니라 정확하다. 부정확해지는 건 배선되는 순간이고,
//     그건 아직 오지 않았다. 이 저장소는 이미 같은 구분을 쓴다: 이 파일 자체가 배선 없이
//     존재하고(§5), poc_full poc-engine.js:79-85의 `order` 파라미터도 unwired 상태로 산다.
//     COST: 매니페스트만 읽는 사람은 "P02는 영원히 LLM-free"로 오해할 수 있다. 그래서
//     p02-6 stub도 지금 넣지 않는다(아래) -- 플래그와 stage 목록이 **같은 시점의 사실**을
//     말하도록 둘을 함께 움직인다.
//     EXIT: p02-6을 배선하는 **바로 그 커밋에서** 함께 바꾼다 -- (i) has_llm_calls -> true,
//     (ii) banner 문구 교체("p02-1~5는 LLM 없이, p02-6만 LLM을 호출합니다" 취지),
//     (iii) 아래 stub 추가. 셋이 갈라지면 UI가 거짓말을 시작한다.
//
//   - **p02-6 매니페스트 stub은 지금 넣지 않는다(설계 문서로만 유지). 판단 근거는 실측된
//     크래시 경로다.** shared/p02-engine.js::collectOverrides()는
//         const mod = moduleForStage[stage.id];
//         Object.assign(overrides[mod], ov.params);
//     인데 moduleForStage에 없는 stage id는 `mod === undefined`가 되고
//     `Object.assign(undefined, ...)`는 TypeError를 던진다(node로 확인:
//     "Cannot convert undefined or null to object"). 즉 `kind:"params"` stub을 넣는 순간
//     **랩 UI에 카드가 하나 생기고, 트레이니가 그 파라미터를 한 번이라도 편집하면 P02 실행
//     전체가 죽는다.** 이건 "designed, not wired"가 아니라 그냥 깨진 상태다.
//     p02-3 제거가 안전했던 이유(목록에서 빼기만 함)와 방향이 반대라는 점에 주의.
//     따라서 stub을 넣으려면 그 전에 collectOverrides()가 미매핑 stage를 건너뛰도록
//     고쳐야 하고(한 줄: `if (!mod) continue;`), 그건 p02-6 배선 작업의 일부지 지금 할 일이
//     아니다 -- 여기서는 shared/ 표면을 두 브랜치에 걸쳐 최소로만 움직인다.
//     넣게 될 때의 모양(제거된 p02-3의 구조를 그대로 따르되 kind는 prompt 계열):
//       { "id": "p02-6", "title": "코드 분석 문서", "kind": "prompt",
//         "function": "(브라우저: LabLLM.chatJSON)",
//         "truncation": { "code_block": 12000, "findings_block": 6000 },
//         "params": { "max_tokens": 2400, "temperature": 0.0 },
//         "system": ..., "user_template": ... }   // 문구는 §4
const CodeLocate = (() => {
  const CONTEXT_LINES = 2;    // 산정된 범위 위아래로 더 보여줄 줄 수
  const BLOCK_MAX_LINES = 40; // 블록 끝 추정 상한
  // D-ground1 §3(a) + D-ground1m: 실측값. 62파일/14,855줄 실제 코퍼스에서 심각 모호
  // (>=5match)가 L=4..8의 13.3% -> L=9..13의 3.7%로 꺾이는 지점. 손으로 고른 8을 대체한다.
  // 측정 방법·전체 수치·재보정 조건은 이 파일 상단 §3의 D-ground1m 블록 참조.
  const MIN_SYMBOL_LEN = 9;

  function splitLines(content) {
    return String(content).split(/\r\n|\r|\n/);
  }

  function normalizeForMatch(s) {
    return String(s).replace(/\s+/g, " ").trim();
  }

  /**
   * files 맵에서 refFile을 찾는다. 정확한 경로 우선, 없으면 베이스네임 폴백.
   * 폴백은 P02Engine.findFileByBasename(D179/D-fix8의 결정론적 정렬)을 재사용한다 --
   * 같은 규칙을 두 번 구현하면 그게 곧 드리프트원이다. node --test에서는 P02Engine
   * 전역이 없으므로 resolver를 주입할 수 있게 열어둔다(테스트가 실제 구현을 쓰게 하기 위함).
   */
  function resolveFile(files, refFile, resolver) {
    if (!refFile) return null;
    if (Object.prototype.hasOwnProperty.call(files, refFile)) return refFile;
    const byBasename = resolver
      || (typeof P02Engine !== "undefined" && P02Engine.findFileByBasename)
      || null;
    if (!byBasename) return null;
    return byBasename(files, String(refFile).split("/").pop());
  }

  /** 들여쓰기 기반 블록 끝 추정. 시작 줄은 문자열 매치로 확정된 사실이라 항상 정확하다. */
  function estimateBlockEnd(lines, startIdx) {
    const baseIndent = (lines[startIdx].match(/^\s*/) || [""])[0].length;
    let endIdx = startIdx;
    for (let i = startIdx + 1; i < lines.length && i - startIdx < BLOCK_MAX_LINES; i++) {
      const line = lines[i];
      if (!line.trim()) { endIdx = i; continue; }
      const indent = (line.match(/^\s*/) || [""])[0].length;
      if (indent <= baseIndent) break;
      endIdx = i;
    }
    while (endIdx > startIdx && !lines[endIdx].trim()) endIdx--;
    return endIdx;
  }

  /**
   * 실제 코드 한 줄(symbol)이 파일의 몇 번째 줄인지 우리가 직접 찾는다(D-ground1 §2).
   * @returns {{valid:true, file, lines:[start,end], matchedLine:number,
   *            ambiguous:boolean, matchCount:number, matchedBy:"exact"|"normalized"}
   *          |{valid:false, reason:string, fileResolved:string|null}}
   */
  function locateSymbol(files, refFile, symbol, opts = {}) {
    const resolved = resolveFile(files, refFile, opts.resolveByBasename);
    if (!resolved) return { valid: false, reason: `파일을 찾을 수 없음: ${refFile}`, fileResolved: null };

    const needle = String(symbol == null ? "" : symbol).trim();
    if (!needle) return { valid: false, reason: "symbol이 비어있음", fileResolved: resolved };

    // D-ground1 §3(a): 짧은 심볼은 "못 찾음"이 아니라 "조용한 오답"을 만든다 -- 위치로 쓰지 않는다.
    // 파일 자체는 실재하므로 fileResolved는 그대로 돌려준다(호출부가 파일 단위로는 쓸 수 있게).
    if (needle.length < MIN_SYMBOL_LEN) {
      return {
        valid: false,
        reason: `symbol이 너무 짧아 위치를 신뢰할 수 없음(${needle.length}자 < ${MIN_SYMBOL_LEN}자): "${needle}"`,
        fileResolved: resolved,
      };
    }

    const lines = splitLines(files[resolved]);

    let matchedBy = "exact";
    let indices = [];
    for (let i = 0; i < lines.length; i++) if (lines[i].includes(needle)) indices.push(i);

    if (!indices.length) {
      // 공백 정규화 폴백 -- 인용 과정에서 들여쓰기/공백만 달라진 경우를 살린다.
      matchedBy = "normalized";
      const normNeedle = normalizeForMatch(needle);
      for (let i = 0; i < lines.length; i++) {
        if (normalizeForMatch(lines[i]).includes(normNeedle)) indices.push(i);
      }
    }

    if (!indices.length) {
      // D-ground1 §6: 여기서 버려진다. 호출부는 이 항목을 화면에 올리지 않는다.
      return {
        valid: false,
        reason: `코드에서 찾을 수 없음: "${needle.slice(0, 60)}"`,
        fileResolved: resolved,
      };
    }

    const startIdx = indices[0];
    const endIdx = estimateBlockEnd(lines, startIdx);
    return {
      valid: true,
      file: resolved,
      lines: [startIdx + 1, endIdx + 1],
      matchedLine: startIdx + 1,
      // D-ground1 §3(b): 버리지 않고 강등 -- 호출부가 "여러 후보 중 하나"라고 정직하게 표시한다.
      ambiguous: indices.length > 1,
      matchCount: indices.length,
      matchedBy,
    };
  }

  /**
   * 산정된 위치를 실제 코드 텍스트로 바꾼다(±CONTEXT_LINES 여유 포함).
   * locateSymbol이 valid를 준 경우에만 호출한다.
   */
  function buildFragment(files, located) {
    if (!located || !located.valid) return null;
    const lines = splitLines(files[located.file]);
    const [start, end] = located.lines;
    const ctxStart = Math.max(1, start - CONTEXT_LINES);
    const ctxEnd = Math.min(lines.length, end + CONTEXT_LINES);
    return {
      file: located.file,
      lines: [start, end],
      contextLines: [ctxStart, ctxEnd],
      text: lines.slice(ctxStart - 1, ctxEnd).join("\n"),
    };
  }

  /**
   * D-ground1 §6의 규율을 한 곳에 모은 진입점: decision_points 배열을 받아
   * grounding에 성공한 것만 남기고, 버린 것은 이유와 함께 따로 돌려준다.
   * 호출부가 filter를 직접 쓰다가 실수로 무효 항목을 화면에 올리는 일을 막는다.
   */
  function groundDecisionPoints(files, decisionPoints, opts = {}) {
    const kept = [];
    const dropped = [];
    for (const dp of Array.isArray(decisionPoints) ? decisionPoints : []) {
      const located = locateSymbol(files, dp && dp.file, dp && dp.symbol, opts);
      if (!located.valid) {
        dropped.push({ point: dp, reason: located.reason });
        continue;
      }
      kept.push({ ...dp, located, fragment: buildFragment(files, located) });
    }
    return { kept, dropped };
  }

  /** 사람이 읽는 참조 표기: "path/to/file.py:12-34" */
  function formatRef(located) {
    if (!located || !located.valid) return "(근거 없음)";
    const [s, e] = located.lines;
    return s === e ? `${located.file}:${s}` : `${located.file}:${s}-${e}`;
  }

  /**
   * D-ground1 §4: code_block을 fan_in 내림차순으로 채운다(알파벳순 금지).
   * 예산 초과 시 stop이 아니라 skip -- 첫 파일이 예산보다 크다고 뒤를 통째로 버리면
   * 결과가 비어버린다(poc_full이 codemap shortlist.py에서 실측한 함정).
   */
  function orderFilesByImportance(files, fanIn) {
    const score = (p) => (fanIn && fanIn[String(p).split("/").pop()]) || 0;
    return Object.keys(files).sort((a, b) => score(b) - score(a) || a.localeCompare(b));
  }

  return {
    locateSymbol, buildFragment, groundDecisionPoints, formatRef,
    orderFilesByImportance, resolveFile,
    MIN_SYMBOL_LEN, CONTEXT_LINES, BLOCK_MAX_LINES,
  };
})();

// 브라우저에는 module이 없어 no-op. node --test(tests/code-locate.test.js)가 이 파일의
// 실제 구현을 그대로 불러 검증하기 위한 한 줄이다 -- 테스트에 로직을 복제하지 않는다.
if (typeof module !== "undefined" && module.exports) module.exports = CodeLocate;
