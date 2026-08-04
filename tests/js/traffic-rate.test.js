// fetchServerTimestamps() 헤더 회귀 테스트 (2026-08-04, redteam audit H8 companion).
//   실행: node --test tests/js/traffic-rate.test.js   (저장소 루트에서)
//
// worker/nvidia-proxy.js의 ?traffic=1이 이제 x-nvidia-api-key를 요구한다(H8) -- 이 파일이
// 헤더 없이 그 엔드포인트를 부르고 있어서, H8만 고치고 이 파일을 안 고치면 여기가 조용히
// 401을 받고 "서버 전체 트래픽 인지" 기능이 탭-로컬 카운트로 몰래 강등된다.
const test = require("node:test");
const assert = require("node:assert");

test("fetchServerTimestamps() sends x-nvidia-api-key when a key is configured", async () => {
  globalThis.LabConfig = {
    get: (key) => ({ "proxy-url": "https://proxy.internal/", "nvidia-key": "nvapi-test-key" }[key]),
  };
  const requests = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    requests.push({ url: String(url), opts });
    return { ok: true, json: async () => ({ timestamps: [1, 2, 3] }) };
  };
  try {
    delete require.cache[require.resolve("../../shared/traffic-rate.js")];
    const DebugTraffic = require("../../shared/traffic-rate.js");
    const result = await DebugTraffic.fetchServerTimestamps();
    assert.deepEqual(result, [1, 2, 3]);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "https://proxy.internal/?traffic=1");
    assert.equal(requests[0].opts.headers["x-nvidia-api-key"], "nvapi-test-key");
  } finally {
    globalThis.fetch = realFetch;
    delete globalThis.LabConfig;
  }
});

test("fetchServerTimestamps() still degrades gracefully (no key configured)", async () => {
  globalThis.LabConfig = { get: (key) => ({ "proxy-url": "https://proxy.internal/" }[key]) };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false });
  try {
    delete require.cache[require.resolve("../../shared/traffic-rate.js")];
    const DebugTraffic = require("../../shared/traffic-rate.js");
    const result = await DebugTraffic.fetchServerTimestamps();
    assert.equal(result, null);
  } finally {
    globalThis.fetch = realFetch;
    delete globalThis.LabConfig;
  }
});
