// D-D (PLAN.md): API keys/PAT live in memory only, optionally sessionStorage -- never
// localStorage, never sent to Supabase, never written into any run record. This file is
// the single place that reads/writes them so that guarantee has one enforcement point.
//
// Supabase URL/anon key are the one exception: this is the team's single shared DB, not a
// per-teammate secret, so they're hardcoded below instead of typed in every session. This
// is safe specifically because Supabase's RLS policies (db/01_members.sql +
// db/02_pdf_analysis.sql) -- not anon key secrecy -- are what actually protects the
// data; the anon key is meant to ship in client code. Teammates still enter their own
// NVIDIA key/PAT and sign in with their own email for per-member attribution.
//
// *** DB: back on the shared popixoxipop-collab project (2026-07-22) ***
// TEAM_SUPABASE_URL/TEAM_SUPABASE_ANON_KEY point at "code-reviewer-pipeline-lab" (ref
// oziaeqcvrkrqkhwrybfj) -- the SAME project Code_reviewer_with_feedback's own docs/lab/
// pages use, reusing its existing `pdf_analysis` schema/tables/RLS (applied back when
// curriculum-manager was still part of that repo, before this branch existed) and its
// already-working Google OAuth (7 real members already sign in there). A short-lived
// dedicated project ("team-iz-curriculum-manager") existed briefly (2026-07-21/22) but
// never accumulated any real data (confirmed via the Management API before switching
// back), so nothing was migrated -- just re-pointed.
// DEFAULT_PROXY_URL below is UNCHANGED and still points at Team-IZ's own dedicated
// Cloudflare Worker ("team-iz-nvidia-proxy", services/nvidia-proxy/ in this repo) -- this switch was
// scoped to the DB only, not the NVIDIA proxy.
const LabConfig = (() => {
  const TEAM_SUPABASE_URL = "https://oziaeqcvrkrqkhwrybfj.supabase.co";
  const TEAM_SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im96aWFlcWN2cmtycWtod3J5YmZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQwMDA4MTksImV4cCI6MjA5OTU3NjgxOX0.hBgzs0V7Nw3WLB8_zNuPDfluYrqOH2_Dto1weQF5iKo";

  // Team-IZ's own deployed proxy (services/nvidia-proxy/nvidia-proxy.js, included in this
  // repo -- see services/nvidia-proxy/wrangler.toml), pre-filled as a default -- unlike the Supabase values above
  // this is NOT force-hardcoded: it's just a starting value in an editable field, so
  // anyone can type in a different proxy's URL at runtime with no code change.
  const DEFAULT_PROXY_URL = "https://team-iz-nvidia-proxy.popixoxipop.workers.dev";

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
    parts.push(state["nvidia-key"] && state["proxy-url"] ? "P01 실행 가능" : "P01: NVIDIA 키 + 프록시 URL 필요");

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
