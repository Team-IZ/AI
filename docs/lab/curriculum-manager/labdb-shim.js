// D1 (2026-07-21): curriculum-manager's runs were landing in the SAME public.runs/
// public.artifacts tables the whole team's P01/P02/P03 traffic writes to, making this
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

  LabDB.ensureClient = ensureClient;
})();
