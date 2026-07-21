-- Prerequisite for pdf_analysis_schema.sql -- pdf_analysis.runs.member_id references
-- public.members(id), so this must be applied FIRST on a fresh Supabase project (paste
-- into SQL Editor -> Run). Safe to re-run (IF NOT EXISTS guards) except the RLS policies
-- and trigger, which use CREATE without IF NOT EXISTS -- drop them first if re-applying
-- after an edit.
--
-- This is a deliberately trimmed extract of just the `members` table + its auto-create
-- trigger + RLS, taken from popixoxipop-collab/Code_reviewer_with_feedback's
-- experiments/web_lab/supabase_schema.sql. That file also defines runs/stage_events/
-- artifacts/presets tables for the P01/P02/P03 shared Pipeline Lab tool this branch does
-- NOT include (only docs/lab/curriculum-manager/ was ported here) -- applying the full
-- file would create tables with no corresponding UI in this repo. `pdf_analysis_schema.sql`
-- doesn't reference any of those, only `members`.

create table if not exists members (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  email text,
  created_at timestamptz not null default now()
);

-- Auto-create a `members` row the first time someone signs in (Google OAuth), so app
-- code never has to remember to do it and pdf_analysis's RLS always has a matching row
-- to check against auth.uid().
create or replace function public.handle_new_member()
returns trigger as $$
begin
  insert into public.members (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_member();

alter table members enable row level security;
create policy "members read all" on members for select to authenticated using (true);
create policy "members update own" on members for update to authenticated using (id = auth.uid());
