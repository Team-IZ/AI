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

-- D3 (2026-07-22): browsing raw unit_map JSON in the Table Editor's `artifacts` grid
-- (public.artifacts also holds P02 findings/P01 questions rows, mixed together, content
-- column truncated to a preview) isn't practical for scanning curriculum content -- same
-- motivation as D175/D177's p01_questions_view for the shared public.runs/artifacts.
-- One row per unit (not per run) to match p03_turns_view's per-turn granularity, since
-- "unit" is this domain's natural browsing grain, same as "turn" is for P03. Lives in
-- public (not pdf_analysis) schema deliberately, so it shows up in the Table Editor
-- alongside p01_questions_view/p03_turns_view without switching the schema dropdown.
--
-- D4 (2026-07-22): D3's first version only queried pdf_analysis.runs/artifacts, which
-- was empty (curriculum-manager's own DB-saved runs are still zero -- every test run this
-- session was unauthenticated). But real unit_map data already existed: 12 genuine P01
-- runs by real team members, saved to public.runs/artifacts by the ORIGINAL Pipeline Lab
-- P01 tab (curriculum-manager wasn't the only thing ever producing this shape of data --
-- confirmed live via the Management API: 122 units across 12 documents). Rebuilt as a
-- UNION ALL of both sources (`source_tool` column distinguishes which one) instead of
-- picking one -- "커리큘럼과 관련된 항목만 모아서" meant everything of this shape, not just
-- this one tool's own future runs. DROP+CREATE (not CREATE OR REPLACE) because the new
-- source_tool column sits in the middle of the existing column order and Postgres won't
-- let CREATE OR REPLACE VIEW reorder/insert columns, only append at the end.
create view public.pdf_analysis_units_view as
with combined as (
  select r.id as run_id, r.member_id, r.model, r.status, r.started_at,
         r.input_meta->>'source_filename' as source_filename,
         a.content as unit_map_content, 'curriculum-manager (pdf_analysis)' as source_tool
  from pdf_analysis.runs r
  join pdf_analysis.artifacts a on a.run_id = r.id and a.kind = 'unit_map'
  where r.pipeline = 'p01'
  union all
  select r.id, r.member_id, r.model, r.status, r.started_at,
         r.input_meta->>'source_filename',
         a.content, 'Pipeline Lab P01 tab (public)' as source_tool
  from public.runs r
  join public.artifacts a on a.run_id = r.id and a.kind = 'unit_map'
  where r.pipeline = 'p01'
)
select
  c.run_id,
  m.email,
  m.display_name,
  coalesce(c.source_filename, '(파일명 미기록)') as source_material,
  c.source_tool,
  c.model,
  c.status,
  c.started_at,
  unit.key as unit_id,
  unit.value->>'unit_title' as unit_title,
  (select min(p::int) from jsonb_array_elements_text(unit.value->'source_pages') p) as page_start,
  (select max(p::int) from jsonb_array_elements_text(unit.value->'source_pages') p) as page_end,
  coalesce(jsonb_array_length(unit.value->'concepts'), 0) as concept_count,
  coalesce(jsonb_array_length(unit.value->'code_examples'), 0) as code_example_count,
  coalesce(jsonb_array_length(unit.value->'cautions'), 0) as caution_count,
  (select string_agg(cn->>'name', ', ' order by ord)
     from jsonb_array_elements(unit.value->'concepts') with ordinality as t(cn, ord)) as concept_names
from combined c
left join public.members m on m.id = c.member_id
cross join lateral jsonb_each(c.unit_map_content) as unit(key, value)
order by c.started_at desc, unit.key;

grant select on public.pdf_analysis_units_view to authenticated;
