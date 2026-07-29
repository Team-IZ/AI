-- D5 (2026-07-28): public.runs/public.presets의 CHECK 제약이 pipeline을
-- ('p01','p02','p03')로만 한정하고 있다 (원본 experiments/web_lab/supabase_schema.sql).
-- 이 PoC(p04)가 그 테이블에 기록을 남기려면 'p04'를 허용 목록에 추가해야 한다.
--
-- 실측 확인 (2026-07-28, anon key로 라이브 조회): public.runs에 이미 그 제약이 존재함을
-- 원본 저장소의 supabase_schema.sql에서 확인했다. 이 세션은 DDL을 실행할 Management API
-- PAT을 갖고 있지 않으므로, 이 파일은 -- 이 저장소의 기존 컨벤션(experiments/web_lab/*.sql)
-- 대로 -- 사람이 한 번 실행해야 하는 마이그레이션으로 남긴다.
--
-- 적용 전: app/db.js의 P04 저장 함수들은 이 제약에 걸려 계속 실패하지만(23514
-- check_violation), 그 실패는 non-fatal이다 -- 화면 동작·채점·보고서는 DB 저장 없이도
-- 완전히 동작하고, 실패 사실만 onProgress 로그에 남는다(이 저장소의 기존 "DB 저장 실패,
-- 결과는 화면에 남아있음" 패턴과 동일).
--
-- 적용 방법: Supabase Dashboard SQL Editor 또는 Management API로 한 번 실행.
-- CHECK 제약은 ADD CONSTRAINT로 직접 못 바꾸므로 기존 제약을 지우고 다시 만든다.
--
-- 제약 이름은 실측하지 않고 Postgres의 기본 명명 규칙(<table>_<column>_check, 스키마 원본이
-- 인라인 `column type CHECK(...)` 형태라 이 규칙이 적용됨)으로 추정한 값이다. 이름이 다르면
-- 아래 DO 블록이 pg_get_constraintdef로 실제 이름을 찾아 대신 지운다 -- 추정이 틀려도 이
-- 파일이 조용히 아무 일도 안 하고 넘어가는 대신 실제 제약을 찾아 처리한다.

do $$
declare
  cname text;
begin
  select conname into cname
    from pg_constraint
    where conrelid = 'public.runs'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) ilike '%pipeline%p01%';
  if cname is not null then
    execute format('alter table public.runs drop constraint %I', cname);
  end if;
  alter table public.runs add constraint runs_pipeline_check
    check (pipeline in ('p01', 'p02', 'p03', 'p04'));
end $$;

do $$
declare
  cname text;
begin
  select conname into cname
    from pg_constraint
    where conrelid = 'public.presets'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) ilike '%pipeline%p01%';
  if cname is not null then
    execute format('alter table public.presets drop constraint %I', cname);
  end if;
  alter table public.presets add constraint presets_pipeline_check
    check (pipeline in ('p01', 'p02', 'p03', 'p04'));
end $$;
