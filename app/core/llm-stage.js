// p04 매니페스트(app/prompt_manifest.json)의 스테이지 하나를 호출하는 공용 경로.
// 여섯 스테이지(p04-1~p04-6) 전부가 "템플릿 채우기 -> chatJSON -> JSON 파싱"을 그대로
// 반복하므로, 이 반복 하나만 여기 둔다 -- 그 이상의 추상화(재시도 정책, 캐싱 등)는
// 필요해지기 전까지 넣지 않는다.
const POCStage = (() => {
  // D-poc8: LabApp is a singleton with ONE in-memory manifest, and its own loadManifest()
  // always fetches "../prompt_manifest.json" -- from app/*.html that resolves to the repo
  // root's manifest (the p02 pipeline definition P02Engine.run() depends on via
  // LabApp.resolveParam("p02", ...)). This PoC's own p04 pipeline lives in a SEPARATE file
  // (app/prompt_manifest.json) so it doesn't collide with or shadow the vendored p02
  // manifest. Both are needed on the same page (analysis.html runs P02Engine.run() for the
  // scan AND POCStage.call("p04-*", ...) for the PoC's own stages), so this merges the p04
  // pipeline into the object LabApp.loadManifest() already populated, in place, instead of
  // replacing it.
  //   WHY: keeping p02's manifest untouched means every vendored p02-engine.js call site
  //   (LabApp.resolveParam("p02", ...)) keeps working unmodified.
  //   COST: two fetches instead of one, and manifest_version on the loaded object reflects
  //   the root file, not p04's -- a p04 DB row's stamped manifest_version is cosmetically
  //   about the wrong file. Not corrected because nothing reads it back for p04 rows yet.
  //   EXIT: if p04 stages are ever folded into the root prompt_manifest.json directly, this
  //   whole function collapses to a plain LabApp.loadManifest() call.
  let mergedPromise = null;
  async function ensureManifestLoaded() {
    if (!mergedPromise) {
      mergedPromise = (async () => {
        await LabApp.loadManifest();
        const res = await fetch("prompt_manifest.json");
        const p04Manifest = await res.json();
        const manifest = LabApp.getManifest();
        manifest.pipelines.p04 = p04Manifest.pipelines.p04;
        return manifest;
      })();
    }
    return mergedPromise;
  }

  function getStage(stageId) {
    const manifest = LabApp.getManifest();
    const stage = manifest.pipelines.p04.stages.find((s) => s.id === stageId);
    if (!stage) throw new Error(`알 수 없는 p04 stage: ${stageId}`);
    return stage;
  }

  // D-poc9: NOT LabApp.resolveParam() -- that reads LabApp's own `overrides` object, which
  // is hardcoded to `{ p02: {}, p03: {} }` (shared/lab-core.js, vendored/drift-checked, not
  // ours to edit). `overrides["p04"]` is undefined there, so resolveParam("p04", ...) throws
  // ("Cannot read properties of undefined"). This is fine to bypass rather than fork the
  // vendored file: p04 never got a stage-card prompt/param editor UI (out of scope, see
  // app/index.html/analysis.html), so there is never an override to resolve -- "look up the
  // override, fall back to default" correctly degenerates to "just the default" here.
  function paramDefault(stageId, key) {
    const stage = getStage(stageId);
    const p = stage.params && stage.params.find((x) => x.key === key);
    return p ? p.default : undefined;
  }

  /**
   * @param {string} stageId          예: "p04-1"
   * @param {object} values           user_template의 {placeholder}를 채울 값
   * @param {object} opts
   * @param {string} opts.model
   * @param {function} [opts.onProgress]
   * @param {number}  [opts.maxAttempts]
   * @returns {Promise<object>} 파싱된 JSON 응답
   */
  async function call(stageId, values, opts = {}) {
    const stage = getStage(stageId);
    for (const key of stage.required_placeholders || []) {
      if (values[key] === undefined || values[key] === null || values[key] === "") {
        throw new Error(`${stageId}(${stage.title}): 필수 값 누락 -- ${key}`);
      }
    }
    const user = LabApp.fillTemplate(stage.user_template, values);
    const maxTokens = paramDefault(stageId, "max_tokens");
    const temperature = paramDefault(stageId, "temperature");
    const resp = await LabLLM.chatJSON({
      model: opts.model,
      messages: [{ role: "system", content: stage.system }, { role: "user", content: user }],
      maxTokens, temperature,
      maxAttempts: opts.maxAttempts,
      onProgress: opts.onProgress,
    });
    return LabLLM.extractJsonObject(resp.content);
  }

  return { ensureManifestLoaded, getStage, paramDefault, call };
})();
