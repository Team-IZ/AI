-- D1 (2026-07-21): pdf_analysis schema -- structurally separates curriculum-manager's
-- P01 runs from the shared team pipeline's public.runs/artifacts, within the SAME
-- Supabase project (not a new project -- schema separation gives the same "not mixed
-- together" outcome as a new project, without a new Google OAuth redirect-URI step or a
-- separate login session, since auth stays project-wide).
-- member_id still references public.members(id) -- auth identity is shared across the
-- whole tool; only the domain data (runs/artifacts) is namespaced.
--
-- Already applied live (2026-07-21, via Supabase Management API against project
-- oziaeqcvrkrqkhwrybfj) -- this file exists for reproducibility/reference, same
-- convention as supabase_schema.sql. Safe to re-run (IF NOT EXISTS guards) except the
-- RLS policies, which use CREATE POLICY without IF NOT EXISTS -- drop them first if
-- re-applying after editing a policy.
--
-- After running this, the schema must also be added to the project's exposed schemas
-- (PostgREST db_schema config -- Dashboard: Settings > API > Data API, or Management
-- API PATCH /v1/projects/{ref}/postgrest with db_schema including "pdf_analysis"), or
-- the REST API returns 404s for it even though the tables exist.
create schema if not exists pdf_analysis;

create table if not exists pdf_analysis.runs (
  id uuid primary key default gen_random_uuid(),
  member_id uuid not null references public.members(id) on delete cascade,
  pipeline text not null check (pipeline in ('p01')),
  model text,
  status text not null default 'done',
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  input_meta jsonb not null default '{}'::jsonb,
  overrides jsonb not null default '{}'::jsonb,
  rubric_overridden boolean not null default false,
  manifest_version text,
  error text,
  created_at timestamptz not null default now()
);
create index if not exists pdf_analysis_runs_member_id_idx on pdf_analysis.runs(member_id);
create index if not exists pdf_analysis_runs_created_at_idx on pdf_analysis.runs(created_at desc);

create table if not exists pdf_analysis.artifacts (
  id bigserial primary key,
  run_id uuid not null references pdf_analysis.runs(id) on delete cascade,
  kind text not null, -- unit_map | graph | questions | refine_fixes
  content jsonb not null default '{}'::jsonb,
  truncated boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists pdf_analysis_artifacts_run_id_idx on pdf_analysis.artifacts(run_id);

alter table pdf_analysis.runs enable row level security;
alter table pdf_analysis.artifacts enable row level security;

-- Same RLS shape as supabase_schema.sql: any authenticated team member can read
-- everything (this is a comparison/review tool), writes restricted to your own member_id.
create policy "pdf_analysis runs read all" on pdf_analysis.runs for select to authenticated using (true);
create policy "pdf_analysis runs insert own" on pdf_analysis.runs for insert to authenticated with check (member_id = auth.uid());
create policy "pdf_analysis runs update own" on pdf_analysis.runs for update to authenticated using (member_id = auth.uid());
-- D2 (2026-07-21): added for the list tab's "− 교안 삭제" button. "own" only (not "read
-- all"'s everyone-can-see shape) -- deleting a teammate's analysis out from under them
-- needs to stay impossible even though everyone can view it. Client-side
-- (curriculum-manager/index.html deleteCurriculum()) treats a 0-row-affected delete
-- (this policy silently blocking someone else's row, not an error) as a failure and
-- reverts its optimistic local removal.
create policy "pdf_analysis runs delete own" on pdf_analysis.runs for delete to authenticated using (member_id = auth.uid());

create policy "pdf_analysis artifacts read all" on pdf_analysis.artifacts for select to authenticated using (true);
create policy "pdf_analysis artifacts insert own" on pdf_analysis.artifacts for insert to authenticated
  with check (exists (select 1 from pdf_analysis.runs where pdf_analysis.runs.id = run_id and pdf_analysis.runs.member_id = auth.uid()));
-- D2: runs' `on delete cascade` FK only auto-removes artifacts rows without needing this
-- policy too when the deleting role bypasses RLS (e.g. table owner) -- Supabase's
-- `authenticated` role does not, so the cascade is itself subject to RLS on artifacts.
-- Without this, deleting your own run could leave its artifacts rows orphaned instead of
-- cascading.
create policy "pdf_analysis artifacts delete own" on pdf_analysis.artifacts for delete to authenticated
  using (exists (select 1 from pdf_analysis.runs where pdf_analysis.runs.id = run_id and pdf_analysis.runs.member_id = auth.uid()));

-- PostgREST only serves schemas explicitly granted to anon/authenticated roles.
grant usage on schema pdf_analysis to anon, authenticated;
grant all on all tables in schema pdf_analysis to anon, authenticated;
grant all on all sequences in schema pdf_analysis to anon, authenticated;
alter default privileges in schema pdf_analysis grant all on tables to anon, authenticated;
alter default privileges in schema pdf_analysis grant all on sequences to anon, authenticated;
