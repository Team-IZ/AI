// D-B (PLAN.md): Supabase Postgres+Auth+RLS as the team DB. Optional -- if not
// configured, pipelines still run fully, results just aren't persisted (console-only).
// Loaded lazily (supabase-js only fetched if the user actually filled in the two fields)
// so the common "just testing locally" path never pulls in a third-party script at all.
const LabDB = (() => {
  let client = null;
  let loadingPromise = null;

  function isConfigured() {
    return Boolean(LabConfig.get("supabase-url") && LabConfig.get("supabase-anon-key"));
  }

  async function ensureClient() {
    if (client) return client;
    if (!isConfigured()) throw new Error("Supabase가 설정되지 않음");
    if (!loadingPromise) {
      loadingPromise = (async () => {
        if (!window.supabase) {
          await loadScript(
            "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js",
            "sha384-GFr3yTh5lJznCbZfpTtXnwboFsxqtTQoeTZCRHhE0579KrRmlCzen5AA8ohaB5ug"
          );
        }
        // D-L (2026-07-15, README D147): flowType left at supabase-js's default ("implicit")
        // meant the magic link's token gets consumed by whoever's request reaches
        // GoTrue's /verify first -- confirmed via project auth logs: multiple /verify
        // hits landed within a 4-second window, all failing "One-time token not found",
        // the signature of an email security scanner (Safe Links-style prefetching)
        // racing the real click and winning. PKCE flow closes this: the link carries a
        // `code`, not a usable token, and completing sign-in requires a code_verifier
        // that only the browser which originally called signInWithOtp() has (in its own
        // localStorage) -- a server-side scanner fetching the URL can't produce that, so
        // it can't consume the real session. detectSessionInUrl (default true, unchanged)
        // already handles the code exchange automatically, so no other app code changes.
        client = window.supabase.createClient(LabConfig.get("supabase-url"), LabConfig.get("supabase-anon-key"), {
          auth: { flowType: "pkce" },
        });
      })();
    }
    await loadingPromise;
    return client;
  }

  function loadScript(src, integrity) {
    return new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = src;
      if (integrity) {
        el.integrity = integrity;
        el.crossOrigin = "anonymous";
      }
      el.onload = resolve;
      el.onerror = () => reject(new Error(`스크립트 로드 실패: ${src}`));
      document.head.appendChild(el);
    });
  }

  async function currentMember() {
    const c = await ensureClient();
    const { data, error } = await c.auth.getUser();
    if (error || !data.user) throw new Error("로그인이 필요합니다 (Supabase magic link)");
    return data.user;
  }

  // D175 (2026-07-15): status/error now overridable (default unchanged: "done", null) --
  // every prior caller passed neither, so this is additive, not a breaking change to any
  // existing call site. Added so a run that throws before ever reaching a success-shaped
  // maybeSaveRun() can still leave a row behind -- see LabApp.saveFailedRun() in app.js,
  // the actual place this gets used.
  //
  // D193 (2026-07-16): `run_id` param added (optional, still additive -- every existing
  // caller omits it and gets the exact insert-only behavior above, unchanged). When
  // present, UPDATEs that runs row instead of inserting a new one -- the other half of
  // this same run's lifecycle now starts with startRun() below and logs turns via
  // logTurn() as they happen, so by the time saveRun() runs there's already a row to
  // finish rather than create. See startRun()'s own comment for the full WHY/COST/EXIT --
  // this function's change is the "finish" half of that same decision.
  async function saveRun({ run_id, pipeline, model, input_meta, overrides, rubric_overridden, artifacts, started_at, finished_at, status, error }) {
    const c = await ensureClient();
    let run;
    if (run_id) {
      const { data, error: updErr } = await c
        .from("runs")
        .update({
          status: status || "done", error: error || null,
          finished_at: finished_at || new Date().toISOString(),
          rubric_overridden: Boolean(rubric_overridden),
        })
        .eq("id", run_id)
        .select()
        .single();
      if (updErr) throw updErr;
      run = data;
    } else {
      const user = await currentMember();
      const { data, error: insErr } = await c
        .from("runs")
        .insert({
          member_id: user.id, pipeline, model, status: status || "done", error: error || null,
          started_at: started_at || new Date().toISOString(),
          finished_at: finished_at || new Date().toISOString(),
          input_meta: input_meta || {}, overrides: overrides || {}, rubric_overridden: Boolean(rubric_overridden),
          manifest_version: (LabApp.getManifest() || {}).manifest_version || null,
        })
        .select()
        .single();
      if (insErr) throw insErr;
      run = data;
    }

    for (const a of artifacts || []) {
      const content = JSON.stringify(a.content);
      const truncated = content.length > 100000;
      const { error: artErr } = await c.from("artifacts").insert({
        run_id: run.id, kind: a.kind,
        content: JSON.parse(truncated ? content.slice(0, 100000) : content === "" ? "{}" : content || "{}"),
        truncated,
      });
      if (artErr) throw artErr;
    }
    return run;
  }

  // D193 (2026-07-16): P03's turn loop used to keep every answered turn in a plain JS
  // array and write it to `artifacts` only once, after the whole loop (all turns) plus
  // grading finished (see p03-engine.js's old single maybeSaveRun() call). Any error
  // partway through -- including turn 3 of 4 -- discarded every turn already answered,
  // because the catch-block fallback (LabApp.saveFailedRun) hardcodes `artifacts: []`.
  // Root-caused live: a team member's P03 run errored on NVIDIA auth; happened to be 0
  // turns in so nothing was actually lost that time, but the structural risk is real for
  // a multi-turn flow a human answers by hand.
  //   WHY: `stage_events` already existed in the schema for exactly this (0 rows anywhere
  //        before this change) and RLS already grants insert-own on it -- no new policy
  //        needed, just start using the table.
  //   COST: one extra DB round-trip per turn (max 4) plus one at session start -- text
  //         payloads, not a real latency concern next to the LLM calls already in the loop.
  //   EXIT: if `stage_events` gets a different primary use later, split into a dedicated
  //         table; the read side (p03_progress_view) would just repoint its JOIN.
  async function startRun({ pipeline, model, input_meta, overrides }) {
    const c = await ensureClient();
    const user = await currentMember();
    const { data: run, error } = await c
      .from("runs")
      .insert({
        member_id: user.id, pipeline, model, status: "running",
        started_at: new Date().toISOString(),
        input_meta: input_meta || {}, overrides: overrides || {},
        manifest_version: (LabApp.getManifest() || {}).manifest_version || null,
      })
      .select()
      .single();
    if (error) throw error;
    return run;
  }

  async function logTurn({ run_id, stage_id, seq, output, latency_ms }) {
    const c = await ensureClient();
    const { error } = await c.from("stage_events").insert({
      run_id, stage_id, seq, output: output || {}, latency_ms: latency_ms ?? null,
    });
    if (error) throw error;
  }

  // D149 (2026-07-15): magic-link email sign-in (D-B/D147/D148's signInWithEmail) removed
  // -- Google OAuth verified working end-to-end (D148) and is now the only login path.
  // Full-page redirect (signInWithOAuth's default), same PKCE flowType already set in
  // ensureClient() above, so the callback's code exchange is handled automatically.
  //
  // D150 (2026-07-15): explicit redirectTo added after a real failure -- without it,
  // supabase-js sent no redirect_to at all to /auth/v1/authorize (confirmed by tracing
  // the actual network request), and despite the project's site_url being correctly
  // https://.../docs/lab/ (reconfirmed live via Management API), the post-login redirect
  // landed on the bare https://popixoxipop-collab.github.io/ root -- a 404 (screenshotted
  // live by the user, mid Google consent flow). Whatever default resolution Supabase uses
  // server-side when redirect_to is omitted didn't match its own documented "falls back
  // to site_url" behavior in this case, so this stops depending on that default entirely.
  // origin+pathname (not full href) so any leftover query/hash from an earlier OAuth
  // attempt on this same tab isn't carried into the value being requested; matches
  // uri_allow_list's entries exactly (prod: exact path, local: localhost:8712/lab/**).
  async function signInWithGoogle() {
    const c = await ensureClient();
    const redirectTo = window.location.origin + window.location.pathname;
    const { error } = await c.auth.signInWithOAuth({ provider: "google", options: { redirectTo } });
    if (error) throw error;
  }

  // D151 (2026-07-15): "로그인 된 상태인지 안 가" -- there was genuinely no UI anywhere
  // that reflected actual session state (renderStatus() in config.js only ever checked
  // NVIDIA-key/proxy-url presence, never LabDB's own session). currentMember() throws by
  // design (saveRun() wants that), so this is a non-throwing variant for status display.
  async function currentMemberOrNull() {
    try {
      return await currentMember();
    } catch (e) {
      // D152: currentMember() throws on the ordinary "nobody's logged in" case too, so
      // this can't distinguish that from a real failure (bad code exchange, network
      // error, misconfigured client) just by swallowing everything silently -- log it so
      // an actual bug is visible in devtools instead of looking identical to "not logged
      // in yet" on screen.
      console.error("[LabDB] currentMemberOrNull:", e);
      return null;
    }
  }

  async function signOut() {
    const c = await ensureClient();
    const { error } = await c.auth.signOut();
    if (error) throw error;
  }

  return { isConfigured, ensureClient, currentMember, currentMemberOrNull, saveRun, startRun, logTurn, signInWithGoogle, signOut };
})();
