// D-owncommit1 (app/stage2-analysis/own-commit.js) 순수 로직 + 네트워크 경로 테스트.
//   실행: node --test tests/own-commit.test.js   (저장소 루트에서)
//
// **실제 네트워크를 절대 건드리지 않는다.** own-commit.js는 opts.call 같은 주입구가 없고
// 전역 fetch를 직접 부르므로(브라우저 전역이라 그렇게 짜여 있다), 이 파일이 각 테스트마다
// globalThis.fetch를 스텁으로 바꿔치기하고 끝나면 원복한다. P02Engine.githubRateLimitError도
// 같은 이유로 스텁을 준다(shared/p02-engine.js를 통째로 로드하지 않는다 -- 이 테스트가 보려는
// 건 own-commit.js가 그 함수를 "제대로 재사용하는가"이지 그 함수 자체의 동작이 아니다).
const test = require("node:test");
const assert = require("node:assert");

const OwnCommit = require("../app/stage2-analysis/own-commit.js");
const CC = require("../app/stage2-analysis/code-candidates.js");

const _origFetch = globalThis.fetch;
const _origP02 = globalThis.P02Engine;

function withFetch(impl, fn) {
  globalThis.fetch = impl;
  globalThis.P02Engine = {
    // D192 실제 판단 로직을 재구현하지 않는다 -- own-commit.js가 이 함수를 "부르는지"만 본다.
    githubRateLimitError(res) {
      if (res.status === 403 && res.headers && res.headers.get("x-ratelimit-remaining") === "0") {
        return new Error("GitHub API 한도 초과(테스트 스텁)");
      }
      return null;
    },
  };
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      globalThis.fetch = _origFetch;
      globalThis.P02Engine = _origP02;
    });
}

function jsonResponse(body, { ok = true, status = 200, headers = {} } = {}) {
  return {
    ok,
    status,
    headers: { get: (k) => headers[k] },
    json: async () => body,
  };
}

// ── classify() 경계값 ────────────────────────────────────────────────────────
test("classify: 0.9 이상은 AUTHORED, 0 초과는 MODIFIED, 0은 UNTOUCHED", () => {
  assert.strictEqual(OwnCommit.classify(1.0), "AUTHORED");
  assert.strictEqual(OwnCommit.classify(0.9), "AUTHORED");
  assert.strictEqual(OwnCommit.classify(0.899), "MODIFIED");
  assert.strictEqual(OwnCommit.classify(0.1), "MODIFIED");
  assert.strictEqual(OwnCommit.classify(0), "UNTOUCHED");
});

// ── 입력 가드 ────────────────────────────────────────────────────────────────
test("email/owner/repo/branch/paths 중 하나라도 없으면 네트워크를 타지 않고 빈 객체를 돌려준다", async () => {
  let called = false;
  await withFetch(async () => { called = true; return jsonResponse({}); }, async () => {
    const r1 = await OwnCommit.fetchOwnCommitSignals({ owner: "o", repo: "r", branch: "main", paths: ["a.py"], email: null, pat: "x" });
    const r2 = await OwnCommit.fetchOwnCommitSignals({ owner: "o", repo: "r", branch: "main", paths: [], email: "a@b.com", pat: "x" });
    assert.deepStrictEqual(r1, {});
    assert.deepStrictEqual(r2, {});
    assert.strictEqual(called, false);
  });
});

// ── PAT 있음 -> GraphQL 배치 경로 ──────────────────────────────────────────────
test("PAT 있으면 GraphQL로 가고, 파일별 blame ranges에서 confidence를 산정한다", async () => {
  await withFetch(async (url, init) => {
    assert.strictEqual(url, "https://api.github.com/graphql");
    assert.strictEqual(init.method, "POST");
    assert.match(init.headers.authorization, /^Bearer /);
    const body = JSON.parse(init.body);
    assert.match(body.query, /blame\(path:/);
    // 두 파일: pay.py(전부 내 라인=AUTHORED), util.py(절반만 내 라인=MODIFIED)
    return jsonResponse({
      data: {
        repository: {
          ref: {
            target: {
              f0: { ranges: [{ startingLine: 1, endingLine: 10, commit: { author: { email: "me@x.com" } } }] },
              f1: { ranges: [
                { startingLine: 1, endingLine: 5, commit: { author: { email: "me@x.com" } } },
                { startingLine: 6, endingLine: 10, commit: { author: { email: "other@x.com" } } },
              ] },
            },
          },
        },
      },
    });
  }, async () => {
    const sig = await OwnCommit.fetchOwnCommitSignals({
      owner: "o", repo: "r", branch: "main",
      paths: ["src/pay.py", "src/util.py"], email: "me@x.com", pat: "ghp_xxx",
    });
    assert.strictEqual(sig["src/pay.py"].attribution_type, "AUTHORED");
    assert.strictEqual(sig["src/pay.py"].confidence, OwnCommit.WEIGHT_BY_TYPE.AUTHORED);
    assert.strictEqual(sig["src/util.py"].attribution_type, "MODIFIED");
    assert.strictEqual(sig["src/util.py"].confidence, OwnCommit.WEIGHT_BY_TYPE.MODIFIED);
  });
});

test("GraphQL이 HTTP 실패를 돌려주면 예외 없이 빈 결과로 강등된다(D6식)", async () => {
  const progressLog = [];
  await withFetch(async () => jsonResponse({}, { ok: false, status: 502 }), async () => {
    const sig = await OwnCommit.fetchOwnCommitSignals({
      owner: "o", repo: "r", branch: "main", paths: ["a.py"], email: "me@x.com", pat: "ghp_xxx",
      onProgress: (m) => progressLog.push(m),
    });
    assert.deepStrictEqual(sig, {});
    assert.ok(progressLog.some((m) => m.includes("502")));
  });
});

test("GraphQL 네트워크 자체가 던지면(fetch reject) 예외를 위로 던지지 않는다", async () => {
  await withFetch(async () => { throw new Error("network down"); }, async () => {
    const sig = await OwnCommit.fetchOwnCommitSignals({
      owner: "o", repo: "r", branch: "main", paths: ["a.py"], email: "me@x.com", pat: "ghp_xxx",
    });
    assert.deepStrictEqual(sig, {});
  });
});

// ── PAT 없음 -> REST 폴백 경로 ─────────────────────────────────────────────────
test("PAT 없으면 REST로 가고(파일당 1콜), author email 일치 비율로 confidence를 근사한다", async () => {
  const calledUrls = [];
  await withFetch(async (url) => {
    calledUrls.push(url);
    if (url.includes("pay.py")) {
      return jsonResponse([
        { commit: { author: { email: "me@x.com" } } },
        { commit: { author: { email: "me@x.com" } } },
      ]);
    }
    return jsonResponse([
      { commit: { author: { email: "other@x.com" } } },
      { commit: { author: { email: "me@x.com" } } },
    ]);
  }, async () => {
    const sig = await OwnCommit.fetchOwnCommitSignals({
      owner: "o", repo: "r", branch: "main",
      paths: ["src/pay.py", "src/util.py"], email: "me@x.com", pat: null,
    });
    assert.strictEqual(calledUrls.length, 2, "REST는 파일당 1콜이어야 한다(D192 COST 그대로)");
    assert.ok(calledUrls[0].includes("/repos/o/r/commits"));
    assert.ok(calledUrls[0].includes("sha=main"));
    assert.strictEqual(sig["src/pay.py"].attribution_type, "AUTHORED"); // 2/2 = 1.0
    assert.strictEqual(sig["src/util.py"].attribution_type, "MODIFIED"); // 1/2 = 0.5
  });
});

test("REST가 rate-limit(403+remaining=0)을 만나면 남은 파일 호출을 멈추고 지금까지 결과만 돌려준다", async () => {
  const calledUrls = [];
  await withFetch(async (url) => {
    calledUrls.push(url);
    if (calledUrls.length === 1) {
      return jsonResponse([{ commit: { author: { email: "me@x.com" } } }]);
    }
    return jsonResponse({ message: "rate limited" }, { ok: false, status: 403, headers: { "x-ratelimit-remaining": "0" } });
  }, async () => {
    const sig = await OwnCommit.fetchOwnCommitSignals({
      owner: "o", repo: "r", branch: "main",
      paths: ["a.py", "b.py", "c.py"], email: "me@x.com", pat: null,
    });
    assert.strictEqual(calledUrls.length, 2, "두 번째 호출에서 한도 초과를 만나면 세 번째는 시도하지 않는다");
    assert.deepStrictEqual(Object.keys(sig), ["a.py"]);
  });
});

test("REST에서 개별 파일 실패(history 없음 등, rate-limit 아님)는 건너뛰고 나머지는 계속 진행한다", async () => {
  const calledUrls = [];
  await withFetch(async (url) => {
    calledUrls.push(url);
    if (calledUrls.length === 1) return jsonResponse({}, { ok: false, status: 404 });
    return jsonResponse([{ commit: { author: { email: "me@x.com" } } }]);
  }, async () => {
    const sig = await OwnCommit.fetchOwnCommitSignals({
      owner: "o", repo: "r", branch: "main", paths: ["a.py", "b.py"], email: "me@x.com", pat: null,
    });
    assert.strictEqual(calledUrls.length, 2, "404는 rate-limit이 아니므로 계속 진행해야 한다");
    assert.deepStrictEqual(Object.keys(sig), ["b.py"]);
  });
});

// ── rankCandidates() 통합: own_commit 항 ───────────────────────────────────────
globalThis.P02Engine = globalThis.P02Engine || {
  findFileByBasename(files, base) {
    return Object.keys(files).find((p) => p.split("/").pop() === base) || null;
  },
};
const CodeFragment = require("../app/stage2-analysis/code-fragment.js");
const LOCATE = { locate: CodeFragment.locateSymbol };
const FILES = {
  "src/pay.py": [
    "def pay(order, method):",
    "    return charge(order)",
  ].join("\n"),
};

test("opts.ownCommit 없이 부르면 own_commit 항은 0이고 weightSum도 늘지 않는다(기존 호출부 회귀 방지)", () => {
  const cands = [{ source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", meta: {} }];
  const [c] = CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE));
  assert.strictEqual(c.rank_evidence.terms.own_commit, 0);
  assert.strictEqual(Object.prototype.hasOwnProperty.call(c.rank_evidence.weights, "own_commit"), false);
});

test("opts.ownCommit이 있으면 own_commit 항이 신호값으로 채워지고 weights에 own_commit이 노출된다", () => {
  const cands = [{ source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", meta: {} }];
  const [c] = CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE), {
    ownCommit: { "src/pay.py": { attribution_type: "AUTHORED", confidence: OwnCommit.WEIGHT_BY_TYPE.AUTHORED } },
  });
  assert.strictEqual(c.rank_evidence.terms.own_commit, OwnCommit.WEIGHT_BY_TYPE.AUTHORED);
  assert.strictEqual(c.rank_evidence.weights.own_commit, 1.0);
});

test("같은 후보라도 own_commit 신호(AUTHORED)가 있으면 없을 때보다 rank_score가 높다", () => {
  const cands = [{ source: "structural", file: "src/pay.py", symbol: "def pay(order, method):", meta: {} }];
  const [without] = CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE));
  const [withSignal] = CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE), {
    ownCommit: { "src/pay.py": { attribution_type: "AUTHORED", confidence: 1.0 } },
  });
  assert.ok(withSignal.rank_score > without.rank_score);
});
