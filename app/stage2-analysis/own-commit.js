// "누가 이 코드를 커밋했는가" 귀속 신호 -- codemap의
// app/engines/shared/signals.py::AttributionSignal과 반환 모양은 같지만(개념 재사용),
// 코드 공유는 없다: codemap은 백엔드에서 실제 `git clone`+`git blame`/`git log`
// subprocess를 쓰지만, P04는 백엔드가 없고 브라우저에서 GitHub API만 쓸 수 있다
// (D-poc10/D208 등 이 저장소의 다른 fetch 경로와 같은 제약).
//
// D-owncommit1 (2026-08-04): codemap의 feature/own-commit-attribution 병합 논의 중
// P04에도 같은 개념이 필요하다고 판단(저장소 소유자 결정). 이 파일이 그 구현이다.
//   WHY GitHub API로 새로 구현(codemap 모듈 재사용 아님): 위 이유(clone 불가).
//     GraphQL의 `blame(path)` 필드가 REST에는 없는 라인 단위 귀속을 준다 -- 확인은
//     gh api graphql로 실제 쿼리(octocat/Spoon-Knife, 파일 3개 alias 배치)를 돌려서
//     응답 구조를 검증했다(추측 아님).
//   WHY ZIP은 지원 안 하는가: 저장소 정체성 자체가 없어 이 신호를 원천적으로 못 낸다
//     (app/index.html/app/core/poc-engine.js에서 D-zip1과 같은 이유로 ZIP 제출 자체를
//     폐지했다).
//   WHY PAT 있으면 GraphQL, 없으면 REST 폴백(하이브리드, 사용자가 구현 복잡도 증가를
//     인지하고 선택): GraphQL은 비인증 접근 자체가 없다(REST와 다른 GitHub 플랫폼
//     제약) -- PAT 없는 사용자를 완전히 배제하는 대신, 더 비싼 REST 파일당 1콜
//     경로로라도 시도한다.
//   COST: REST 폴백은 shared/p02-engine.js의 D192(실제 403 폭풍 프로덕션 사고,
//     2026-07-16)가 경고하는 바로 그 비용 패턴(파일당 1콜)을 반복한다 -- 그래서
//     아래 두 경로 다 P02Engine.githubRateLimitError()를 그대로 재사용해서 같은
//     방식으로 감지·안내한다(새 실패 모드를 안 만든다). REST는 라인 단위가 아니라
//     "이 파일을 건드린 커밋들의 author 일치 비율"로 근사하므로 GraphQL만큼
//     정밀하지 않다.
//   EXIT: 이 신호를 아예 끄려면 poc-engine.js의 fetchOwnCommitSignals() 호출 한 줄만
//     빼면 된다 -- rankCandidates()는 opts.ownCommit이 없으면 기존 동작과 완전히
//     동일하다(code-candidates.js D-owncommit1 참고).
//
// 실패 처리 원칙: 이 파일의 모든 함수는 예외를 밖으로 던지지 않는다(D6식 강등과
// 같은 철학, 이 저장소 전체가 반복해 온 규율). own-commit 신호가 없어도 스캔/랭킹은
// 끝까지 정상 진행되고, 그 파일은 그냥 이 신호가 빠진 채로 랭킹된다.
const OwnCommit = (() => {
  const GRAPHQL_URL = "https://api.github.com/graphql";
  // GitHub GraphQL의 쿼리 복잡도/노드 한도를 고려한 배치 크기 -- 실측 아님(provisional).
  // 너무 크면 요청 자체가 거부될 수 있고, 너무 작으면 배치의 이점(콜 수 절감)이 준다.
  const GRAPHQL_CHUNK_SIZE = 25;
  const REST_PER_PAGE = 100;

  // codemap의 app/engines/codemap/rank.py::_OWN_COMMIT_WEIGHT_BY_TYPE과 동일한 매핑을
  // 그대로 가져다 쓴다 -- 그쪽도 실측 아닌 provisional 값이지만, 여기서 새로 지어내는
  // 대신 이미 있는 결정을 재사용한다(둘 다 unmeasured라는 사실은 동일하게 남는다).
  const WEIGHT_BY_TYPE = {
    AUTHORED: 1.0,
    MODIFIED: 0.6,
    UNTOUCHED: 0.0,
    UNKNOWN: 0.0,
  };

  /** 일치 비율 -> attribution_type. 경계값(0.9)도 provisional -- 실측 후 조정 대상. */
  function classify(matchRatio) {
    if (matchRatio >= 0.9) return "AUTHORED";
    if (matchRatio > 0) return "MODIFIED";
    return "UNTOUCHED";
  }

  function githubHeaders(pat) {
    const headers = { accept: "application/vnd.github+json" };
    if (pat) headers.authorization = `Bearer ${pat}`;
    return headers;
  }

  function rateLimitMessage(res, pat) {
    if (typeof P02Engine === "undefined" || !P02Engine.githubRateLimitError) return null;
    const err = P02Engine.githubRateLimitError(res, pat);
    return err ? err.message : null;
  }

  /**
   * GraphQL 배치 blame -- 파일 여러 개를 alias로 한 요청에 묶는다(gh api graphql로
   * 실제 검증된 쿼리 구조, 2026-08-04). 청크 단위로 여러 번 요청.
   */
  async function fetchViaGraphQL(owner, repo, branch, paths, email, pat, onProgress) {
    const headers = { ...githubHeaders(pat), "content-type": "application/json" };
    const signals = {};
    for (let i = 0; i < paths.length; i += GRAPHQL_CHUNK_SIZE) {
      const chunk = paths.slice(i, i + GRAPHQL_CHUNK_SIZE);
      const aliasFields = chunk
        .map((p, idx) => `f${idx}: blame(path: ${JSON.stringify(p)}) { ranges { startingLine endingLine commit { author { email } } } }`)
        .join("\n");
      const query = `{
        repository(owner: ${JSON.stringify(owner)}, name: ${JSON.stringify(repo)}) {
          ref(qualifiedName: ${JSON.stringify("refs/heads/" + branch)}) {
            target { ... on Commit { ${aliasFields} } }
          }
        }
      }`;
      let res;
      try {
        res = await fetch(GRAPHQL_URL, { method: "POST", headers, body: JSON.stringify({ query }) });
      } catch (e) {
        if (onProgress) onProgress(`own-commit GraphQL 네트워크 실패(${e.message}) -- 이후 파일 생략`);
        return signals;
      }
      if (!res.ok) {
        const rl = rateLimitMessage(res, pat);
        if (onProgress) onProgress(`own-commit GraphQL 실패(HTTP ${res.status})${rl ? ": " + rl : ""} -- 이후 파일 생략, 스캔은 계속`);
        return signals; // D6식 강등: 지금까지 채운 것만이라도 반환
      }
      const data = await res.json();
      const target = data && data.data && data.data.repository && data.data.repository.ref && data.data.repository.ref.target;
      if (!target) {
        if (onProgress) onProgress("own-commit GraphQL 응답 형태가 예상과 다름 -- 이 신호 생략");
        return signals;
      }
      chunk.forEach((p, idx) => {
        const blame = target[`f${idx}`];
        if (!blame || !Array.isArray(blame.ranges)) return;
        let matched = 0, total = 0;
        for (const r of blame.ranges) {
          const lineCount = Math.max(0, r.endingLine - r.startingLine + 1);
          total += lineCount;
          if (r.commit && r.commit.author && r.commit.author.email === email) matched += lineCount;
        }
        const ratio = total > 0 ? matched / total : 0;
        const attribution_type = classify(ratio);
        signals[p] = { attribution_type, confidence: WEIGHT_BY_TYPE[attribution_type] };
      });
    }
    return signals;
  }

  /**
   * REST 폴백(PAT 없을 때만) -- 파일당 1콜, D192 위험을 그대로 안고 간다. 라인 단위가
   * 아니라 "이 파일을 건드린 커밋 중 author email이 일치하는 비율"로 근사한다(REST엔
   * blame이 없음).
   */
  async function fetchViaRest(owner, repo, branch, paths, email, pat, onProgress) {
    const headers = githubHeaders(pat);
    const signals = {};
    for (const p of paths) {
      const url = `https://api.github.com/repos/${owner}/${repo}/commits?path=${encodeURIComponent(p)}&sha=${encodeURIComponent(branch)}&per_page=${REST_PER_PAGE}`;
      let res;
      try {
        res = await fetch(url, { headers });
      } catch (e) {
        continue; // 개별 파일 네트워크 실패는 건너뛰고 계속(D6식)
      }
      if (!res.ok) {
        const rl = rateLimitMessage(res, pat);
        if (rl) {
          // D192: rate-limit이면 나머지 파일도 다 똑같이 실패할 뿐이다 -- 조용히
          // 하나씩 계속 실패하는 대신 여기서 멈춘다(fetchGithubRepo의 동일 판단과 같음).
          if (onProgress) onProgress(`own-commit REST 한도 초과 -- 남은 파일 생략: ${rl}`);
          break;
        }
        continue; // rate-limit 아닌 개별 실패(예: 파일 히스토리 없음)는 건너뜀
      }
      const commits = await res.json();
      if (!Array.isArray(commits) || !commits.length) continue;
      let matched = 0;
      for (const c of commits) {
        const authorEmail = c.commit && c.commit.author && c.commit.author.email;
        if (authorEmail === email) matched += 1;
      }
      const ratio = matched / commits.length;
      const attribution_type = classify(ratio);
      signals[p] = { attribution_type, confidence: WEIGHT_BY_TYPE[attribution_type] };
    }
    return signals;
  }

  /**
   * @param {object} p
   * @param {string} p.owner
   * @param {string} p.repo
   * @param {string} p.branch  이미 해석된 실제 브랜치(P02Engine.fetchGithubRepo()가
   *   돌려준 것과 같은 값) -- 이 함수가 다시 해석하지 않는다.
   * @param {string[]} p.paths  신호를 구할 파일 경로 목록(저장소 상대 경로)
   * @param {string|null|undefined} p.email  제출자 이메일(LabDB.currentMemberOrNull()의
   *   .email) -- 없으면(로그인 안 함) 신호 자체를 생략
   * @param {string|null|undefined} p.pat  GitHub PAT -- 있으면 GraphQL 배치, 없으면
   *   REST 폴백(D192 위험 감수)
   * @param {(msg:string)=>void} [p.onProgress]
   * @returns {Promise<Object<string,{attribution_type:string, confidence:number}>>}
   *   실패/데이터 없음이면 빈 객체(예외를 던지지 않는다).
   */
  async function fetchOwnCommitSignals({ owner, repo, branch, paths, email, pat, onProgress } = {}) {
    if (!email || !owner || !repo || !branch || !paths || !paths.length) return {};
    try {
      if (pat) return await fetchViaGraphQL(owner, repo, branch, paths, email, pat, onProgress);
      return await fetchViaRest(owner, repo, branch, paths, email, pat, onProgress);
    } catch (e) {
      if (onProgress) onProgress(`own-commit 신호 조회 실패(${e.message}) -- 생략, 스캔은 계속`);
      return {};
    }
  }

  return { fetchOwnCommitSignals, classify, WEIGHT_BY_TYPE };
})();

// 브라우저에서는 전역 OwnCommit으로 쓰이고, 이 줄은 node --test(tests/own-commit.test.js)에서
// 순수 로직을 검증하기 위한 것이다. 브라우저에는 module이 없으므로 no-op.
if (typeof module !== "undefined" && module.exports) module.exports = OwnCommit;
