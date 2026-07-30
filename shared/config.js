// D-D (PLAN.md): API keys/PAT live in memory only, optionally sessionStorage -- never
// localStorage, never sent to Supabase, never written into any run record. This file is
// the single place that reads/writes them so that guarantee has one enforcement point.
//
// Supabase URL/anon key are the one exception: this is the team's single shared DB, not a
// per-teammate secret, so they're hardcoded below instead of typed in every session. This
// is safe specifically because Supabase's RLS policies (defined in the dashboard; schema
// DDL is upstream-only -- Code_reviewer_with_feedback:experiments/web_lab/
// supabase_schema.sql, not vendored here) -- not anon key secrecy -- are what actually
// protects the data; the anon key is meant to ship in
// client code. Teammates still enter their own NVIDIA key/PAT and sign in with their own
// email for per-member attribution.
const LabConfig = (() => {
  // D208: repointed to Team-IZ's own resources for the NVIDIA proxy (Cloudflare Worker)
  // -- unchanged, still team-iz-code-qna-proxy, see the DEFAULT_PROXY_URL comment below.
  //
  // D213 (2026-07-22): Supabase repointed AGAIN -- team-iz-curriculum-manager (D208) was a
  // freshly-created, mostly-empty project (2 members). code-reviewer-pipeline-lab (the
  // ORIGINAL popixoxipop-collab/Code_reviewer_with_feedback repo's own Supabase project)
  // turned out to already hold this team's REAL usage history: 7 real members (confirmed
  // by the user -- not dev/test accounts), 30 real p03 runs, 176 p02 runs, plus
  // convenience views (p03_progress_view, p03_turns_view, runs_with_email,
  // artifacts_with_email) team-iz-curriculum-manager never had.
  //   WHY: this is where the team's actual interview history already lives -- pointing
  //   code-qna at team-iz-curriculum-manager was creating a SECOND, mostly-empty,
  //   disconnected history for the same team instead of continuing the real one.
  //   COST: reintroduces a data dependency on a Supabase project namespaced/created for
  //   the ORIGINAL repo's own Pipeline Lab -- NOT a cross-account/cross-org dependency
  //   though (confirmed both projects sit under the same Supabase organization the user
  //   already controls), so this is a scope/naming concern, not an infra-ownership one
  //   like the Cloudflare Worker/Python-source D208 fix was. Team-IZ trainee runs are now
  //   RLS-visible to (and mixed in the same tables as) whoever else uses that original
  //   project's own Pipeline Lab -- acceptable here specifically because the 5 non-owner
  //   members already there were confirmed to BE the real Team-IZ teammates this tool is
  //   for, not unrelated third parties.
  //   EXIT: if code-reviewer-pipeline-lab is ever retired/inaccessible, revert to a
  //   dedicated Team-IZ-owned project (team-iz-curriculum-manager's runs/stage_events/
  //   artifacts/presets tables are still there, just empty of code-qna activity since this
  //   switch) by reverting these two constants and rerunning D208's Supabase schema note.
  const TEAM_SUPABASE_URL = "https://oziaeqcvrkrqkhwrybfj.supabase.co";
  const TEAM_SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im96aWFlcWN2cmtycWtod3J5YmZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQwMDA4MTksImV4cCI6MjA5OTU3NjgxOX0.hBgzs0V7Nw3WLB8_zNuPDfluYrqOH2_Dto1weQF5iKo";

  // Owner-deployed proxy (worker/nvidia-proxy.js), pre-filled as a default -- unlike the
  // Supabase values above this is NOT force-hardcoded: it's just a starting value in an
  // editable field, since the upstream SETUP.md's team-sharing note (Code_reviewer_with_
  // feedback:experiments/web_lab/SETUP.md) explicitly means for teammates to
  // be able to deploy and swap in their own proxy instead (a URL, not a credential, so
  // there's no reason to also lock this one down).
  // D208: deployed as its own dedicated Worker (team-iz-code-qna-proxy, own KV namespace +
  // queue) from the unmodified worker/nvidia-proxy.js -- distinct from both the original
  // nvidia-proxy and the curriculum-manager task's team-iz-nvidia-proxy, so this tool never
  // shares failure/rate-limit blast radius with either.
  const DEFAULT_PROXY_URL = "https://team-iz-code-qna-proxy.popixoxipop.workers.dev";

  const FIELDS = ["nvidia-key", "proxy-url", "github-pat"];
  const SESSION_PREFIX = "lab_cfg_";
  let state = { "nvidia-key": "", "proxy-url": DEFAULT_PROXY_URL, "github-pat": "" };

  function loadFromSession() {
    if (!sessionStorage.getItem(SESSION_PREFIX + "remember")) return;
    for (const f of FIELDS) {
      const v = sessionStorage.getItem(SESSION_PREFIX + f);
      if (v) {
        state[f] = v;
        const el = document.getElementById(f);
        if (el) el.value = v;
      }
    }
    const rememberBox = document.getElementById("remember-session");
    if (rememberBox) rememberBox.checked = true;
  }

  function wireInputs() {
    for (const f of FIELDS) {
      const el = document.getElementById(f);
      if (!el) continue;
      el.addEventListener("input", () => {
        state[f] = el.value.trim();
        persistIfRemembered();
        renderStatus();
      });
    }
    const rememberBox = document.getElementById("remember-session");
    if (rememberBox) {
      rememberBox.addEventListener("change", () => {
        if (rememberBox.checked) {
          sessionStorage.setItem(SESSION_PREFIX + "remember", "1");
          persistIfRemembered();
        } else {
          sessionStorage.removeItem(SESSION_PREFIX + "remember");
          for (const f of FIELDS) sessionStorage.removeItem(SESSION_PREFIX + f);
        }
      });
    }
  }

  function persistIfRemembered() {
    if (!sessionStorage.getItem(SESSION_PREFIX + "remember")) return;
    for (const f of FIELDS) sessionStorage.setItem(SESSION_PREFIX + f, state[f] || "");
  }

  // D151 (2026-07-15): the "DB 저장..." half of this used to be a fixed string regardless
  // of whether anyone was actually logged in -- no element anywhere reflected the real
  // session, so a successful Google login was visually indistinguishable from a failed
  // one. Now checks LabDB's actual session and shows the signed-in email (or its absence),
  // and toggles which of the login/logout buttons is shown.
  async function renderStatus() {
    const el = document.getElementById("auth-status");
    if (!el) return;
    const parts = [];
    parts.push(state["nvidia-key"] && state["proxy-url"] ? "P03 실행 가능" : "P03: NVIDIA 키 + 프록시 URL 필요");

    const loginBtn = document.getElementById("google-login-btn");
    const logoutBtn = document.getElementById("logout-btn");
    const user = await LabDB.currentMemberOrNull();
    if (user) {
      parts.push(`로그인됨: ${user.email} — DB 저장 켜짐`);
      if (loginBtn) loginBtn.classList.add("hidden");
      if (logoutBtn) { logoutBtn.classList.remove("hidden"); logoutBtn.textContent = `로그아웃 (${user.email})`; }
    } else {
      parts.push("로그인 필요 — 지금은 DB 저장 안 됨");
      if (loginBtn) loginBtn.classList.remove("hidden");
      if (logoutBtn) logoutBtn.classList.add("hidden");
    }

    el.textContent = parts.join(" · ");
    el.className = "auth-status" + (state["nvidia-key"] && state["proxy-url"] ? " ok" : "");
  }

  function get(field) {
    if (field === "supabase-url") return TEAM_SUPABASE_URL;
    if (field === "supabase-anon-key") return TEAM_SUPABASE_ANON_KEY;
    return state[field] || "";
  }
  function has(field) {
    if (field === "supabase-url" || field === "supabase-anon-key") return true;
    return Boolean(state[field]);
  }

  function wireLogin() {
    const statusEl = document.getElementById("login-status");
    const googleBtn = document.getElementById("google-login-btn");
    if (googleBtn) {
      googleBtn.addEventListener("click", async () => {
        statusEl.textContent = "Google로 이동 중...";
        try {
          await LabDB.signInWithGoogle(); // full-page redirect -- nothing runs after this on success
        } catch (err) {
          statusEl.textContent = `실패: ${err.message}`;
        }
      });
    }

    const logoutBtn = document.getElementById("logout-btn");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", async () => {
        try {
          await LabDB.signOut();
          statusEl.textContent = "로그아웃됨";
          renderStatus();
        } catch (err) {
          statusEl.textContent = `실패: ${err.message}`;
        }
      });
    }
  }

  function applyDefaults() {
    for (const f of FIELDS) {
      const el = document.getElementById(f);
      if (el && state[f]) el.value = state[f];
    }
  }

  function init() {
    wireInputs();
    applyDefaults();
    loadFromSession();
    renderStatus();
    wireLogin();
  }

  return { init, get, has, FIELDS };
})();

document.addEventListener("DOMContentLoaded", LabConfig.init);
