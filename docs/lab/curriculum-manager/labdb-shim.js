// D1 (2026-07-21): curriculum-manager's runs were landing in the SAME public.runs/
// public.artifacts tables the whole team's pipeline traffic writes to, making this
// tool's data hard to tell apart from everything else. Considered a whole new Supabase
// project first, but that needs a manual Google Cloud Console step (authorizing the new
// project's OAuth callback URL) and splits auth into a separate login session -- neither
// necessary just to keep data apart. A dedicated Postgres schema in the SAME project
// (pdf_analysis.runs / pdf_analysis.artifacts, see the migration SQL run via the
// Management API, same shape as experiments/web_lab/supabase_schema.sql's public
// tables) gets the same "not mixed together" outcome for free: same auth realm (a
// session from any other lab page already works here), same project, structurally
// separate tables a teammate can't accidentally confuse with public.runs.
//   WHY: schema-per-domain within one database is the standard way to separate an
//     ERD's sub-areas without duplicating infrastructure (org/project/auth/billing).
//   COST: still the same underlying project/database -- a project-wide outage or quota
//     issue affects this table too. Acceptable for a small internal tool; this was never
//     about hard multi-tenant isolation.
//   EXIT: pg_dump --schema=pdf_analysis and restore into a real separate project if hard
//     isolation is ever actually needed later.
//
// db.js's ensureClient() is the ONLY place that constructs the Supabase client, and
// every other LabDB function (saveRun/currentMember/signInWithGoogle/...) calls through
// it -- overriding just this one function, without touching db.js, is enough to route
// every downstream .from("runs")/.from("artifacts") call in db.js AND in this page's own
// index.html on this schema instead of "public", via supabase-js's `db.schema` client
// option. Same Supabase project/URL/anon key as every other lab page (LabConfig already
// has them) -- only the schema differs, so this needs no new credentials at all.
(function () {
  let client = null;
  let loadingPromise = null;

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

  async function ensureClient() {
    if (client) return client;
    if (!LabDB.isConfigured()) throw new Error("Supabase가 설정되지 않음");
    if (!loadingPromise) {
      loadingPromise = (async () => {
        if (!window.supabase) {
          await loadScript(
            "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js",
            "sha384-GFr3yTh5lJznCbZfpTtXnwboFsxqtTQoeTZCRHhE0579KrRmlCzen5AA8ohaB5ug"
          );
        }
        // Same flowType as db.js's own ensureClient() (D-L/PKCE) -- only `db.schema` is
        // new here.
        client = window.supabase.createClient(LabConfig.get("supabase-url"), LabConfig.get("supabase-anon-key"), {
          auth: { flowType: "pkce" },
          db: { schema: "pdf_analysis" },
        });
      })();
    }
    await loadingPromise;
    return client;
  }

  // D3 (2026-07-23): second client, scoped to the DEFAULT ("public") schema instead of
  // pdf_analysis -- needed so this page can also read/delete the pre-existing curricula
  // that were analyzed through the original Pipeline Lab P01 tab (docs/lab/index.html),
  // which writes to public.runs/public.artifacts, not pdf_analysis.*. ensureClient()
  // above can't be reused for this: it's permanently pinned to db.schema:"pdf_analysis",
  // and PostgREST's schema option is fixed per client (no per-query override).
  //   WHY: same Supabase project/URL/anon key/session -- a second createClient() call is
  //     the only way supabase-js exposes a different `db.schema` at the same time.
  //   COST: two GoTrueClient instances against the same URL was NOT just a cosmetic
  //     console warning -- refreshFromDB() calls ensureClient()+ensurePublicClient() in
  //     Promise.all() at page load, which is also the moment the browser lands back here
  //     with `?code=...` after a Google login. supabase-js's `detectSessionInUrl`
  //     defaults to true (confirmed in the shipped bundle), so with no override BOTH
  //     clients raced to consume that same one-time code, and a user hit
  //     bad_oauth_state/"OAuth state not found or expired" shortly after this shipped --
  //     not proven as the only cause (state validation errors can also come from stale
  //     browser state), but a real, confirmed mechanism, so fixed regardless.
  //   EXIT: if a third schema is ever needed, give it the same detectSessionInUrl:false
  //     treatment -- only ONE client on a page should ever be allowed to process the
  //     OAuth callback URL.
  let publicClient = null;
  let publicLoadingPromise = null;

  async function ensurePublicClient() {
    if (publicClient) return publicClient;
    if (!LabDB.isConfigured()) throw new Error("Supabase가 설정되지 않음");
    if (!publicLoadingPromise) {
      publicLoadingPromise = (async () => {
        if (!window.supabase) {
          await loadScript(
            "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js",
            "sha384-GFr3yTh5lJznCbZfpTtXnwboFsxqtTQoeTZCRHhE0579KrRmlCzen5AA8ohaB5ug"
          );
        }
        // detectSessionInUrl:false -- this client only ever reads a session that
        // ensureClient()'s client (the one wireLogin()/signInWithGoogle() actually run
        // through) already established; it must never independently try to process an
        // OAuth callback URL itself. autoRefreshToken:false for the same reason: token
        // lifecycle belongs to the primary client, this one just piggybacks on its
        // shared localStorage session (no custom storageKey on either client).
        publicClient = window.supabase.createClient(LabConfig.get("supabase-url"), LabConfig.get("supabase-anon-key"), {
          auth: { flowType: "pkce", detectSessionInUrl: false, autoRefreshToken: false },
        });
      })();
    }
    await publicLoadingPromise;
    return publicClient;
  }

  // D1 (2026-07-24): 사용자 요청 -- 모델 선택 UI의 "비고"를 하드코딩(lab-core.js의
  // CURATED_MODELS)에서 팀 공유 DB 메모로 전환. public.model_notes 사용(pdf_analysis
  // 아님) -- ensurePublicClient()가 이미 public 스키마 클라이언트라 members(email)
  // embed가 same-schema로 문제없이 동작(pdf_analysis.runs 쪽에서 이미 겪은
  // 크로스스키마 embed 400 함정을 처음부터 피함). 스키마/RLS는
  // experiments/web_lab/model_notes_schema.sql 참고.
  // D3 (2026-07-24): quality_score 등은 codex P01-EQ 채점(30개 모델, DB 밖 파이썬
  // 스크립트가 직접 REST로 upsert)이 채우는 컬럼 -- 이 함수는 읽기만, 쓰기는 없음
  // (saveModelNote는 note 텍스트 전용으로 그대로 둠, 점수는 사람이 UI로 편집하는
  // 대상이 아니라서).
  async function fetchModelNotes() {
    if (!LabDB.isConfigured()) return {};
    try {
      const c = await ensurePublicClient();
      const { data, error } = await c.from("model_notes").select(
        "model_id, note, updated_at, members(email), quality_score, quality_band, quality_axes, quality_scored_at"
      );
      if (error) throw error;
      const map = {};
      for (const row of data || []) {
        map[row.model_id] = {
          note: row.note,
          updatedByEmail: (row.members && row.members.email) || null,
          updatedAt: row.updated_at,
          qualityScore: row.quality_score,
          qualityBand: row.quality_band,
          qualityAxes: row.quality_axes,
          qualityScoredAt: row.quality_scored_at,
        };
      }
      return map;
    } catch (e) {
      console.warn("[LabDB] fetchModelNotes 실패(페이지는 정적 비고로 계속 동작):", e);
      return {};
    }
  }

  // 실패 시 그대로 throw -- 호출부(labapp-shim.js)가 저장 버튼 UI에 실패를 보여줘야
  // 하므로 여기서 삼키지 않음(fetchModelNotes와 반대 -- 그쪽은 페이지 로드시 자동
  // 호출이라 조용히 폴백하는 게 맞고, 이쪽은 사용자가 명시적으로 누른 저장 액션).
  async function saveModelNote(modelId, note) {
    const c = await ensurePublicClient();
    const user = await LabDB.currentMember(); // throws "로그인이 필요합니다" if not logged in
    const updatedAt = new Date().toISOString();
    const { error } = await c.from("model_notes").upsert(
      { model_id: modelId, note, updated_by: user.id, updated_at: updatedAt },
      { onConflict: "model_id" }
    );
    if (error) throw error;
    return { updatedByEmail: user.email, updatedAt };
  }

  LabDB.ensureClient = ensureClient;
  LabDB.ensurePublicClient = ensurePublicClient;
  LabDB.fetchModelNotes = fetchModelNotes;
  LabDB.saveModelNote = saveModelNote;
})();
