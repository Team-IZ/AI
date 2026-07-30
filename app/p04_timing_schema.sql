-- D8 (2026-07-30): frozen vs adaptive 힌트 방식의 질문/힌트 생성 소요시간을 비교해야
-- 한다는 요구 -- public.runs에 실컬럼 2개를 추가한다(JSONB input_meta 필드가 아니라
-- 진짜 컬럼: "DB에 칼럼 구별 지어서 기록"이라는 요구를 문자 그대로 만족시키기 위해).
--
-- hint_mode: 'frozen' | 'adaptive' -- 이 run이 어느 모드로 실행됐는지. 단순 텍스트라
--   CHECK 제약을 걸어 오타를 방지한다(app/scoring-config.js의 POCScoring.hintMode.options
--   와 반드시 동기화 -- 그쪽에 새 모드를 추가하면 이 제약도 같이 넓혀야 한다).
-- timing_ms: JSONB -- 분석 단계는 {hintMode, questionGenMs:[...], frozenHintGenMs:[{axis,lv,ms}]},
--   세션 단계는 {hintMode, adaptiveHintGenMs:[{topicIndex,axis,lv,ms}]}. 세부 항목이 늘어날
--   여지가 있어 컬럼을 늘리는 대신 구조화된 JSONB 하나로 받는다 -- hint_mode만 평면 컬럼으로
--   분리한 건 그게 "frozen만/adaptive만" 필터링·집계의 주 축이기 때문이다.
--
-- 적용 방법: Supabase Dashboard SQL Editor 또는 Management API로 한 번 실행. 이미 있는
-- 컬럼이면 IF NOT EXISTS라 안전하게 재실행 가능.

alter table public.runs add column if not exists hint_mode text;
alter table public.runs add column if not exists timing_ms jsonb;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.runs'::regclass
      and conname = 'runs_hint_mode_check'
  ) then
    alter table public.runs add constraint runs_hint_mode_check
      check (hint_mode is null or hint_mode in ('frozen', 'adaptive'));
  end if;
end $$;

-- p04 run을 hint_mode/소요시간 기준으로 훑어보기 위한 편의 뷰 -- 이 저장소의 기존
-- p01_questions_view/p03_progress_view와 같은 관례(원 테이블은 Table Editor에서 다른
-- 파이프라인 데이터와 섞여 있어 보기 불편함).
create or replace view public.p04_timing_view as
select
  r.id as run_id,
  m.email,
  r.model,
  r.hint_mode,
  r.status,
  r.started_at,
  r.finished_at,
  extract(epoch from (r.finished_at - r.started_at)) * 1000 as wall_ms,
  r.timing_ms
from public.runs r
left join public.members m on m.id = r.member_id
where r.pipeline = 'p04' and r.hint_mode is not null
order by r.started_at desc;

grant select on public.p04_timing_view to authenticated;
