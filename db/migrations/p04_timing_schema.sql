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
--
-- D-fix (redteam audit H6, 2026-08-04): 이 뷰는 원래 security_invoker 지정이 없어
-- PostgreSQL 기본값(definer 권한, 뷰 소유자 권한으로 실행)으로 동작했다 -- runs/members의
-- RLS 정책이 무엇이든, 이 뷰를 통해 조회하면 그 정책을 그대로 우회했다. authenticated
-- 롤 전체에 select를 부여해 뒀으므로, 로그인 계정 하나만 있으면 이 뷰로 전 멤버의 run
-- 기록 + 이메일을 열람할 수 있었다.
--   WHY: security_invoker=true로 호출자 권한(RLS 적용됨)으로 실행하게 바꾸고, 이
--   뷰의 목적("hint_mode별 소요시간 비교")에 불필요한 m.email 컬럼 자체를 뺐다 --
--   PII는 이 뷰가 답해야 할 질문("frozen이 adaptive보다 빠른가")에 필요 없다.
--   COST: member_id 단위 개인 필터(`r.member_id = auth.uid()`)는 넣지 않았다 --
--   이 뷰의 존재 이유가 "여러 멤버의 소요시간을 비교"하는 것이라, 개인 필터를 넣으면
--   뷰 자체가 무의미해진다. security_invoker만으로는 기저 runs/members 테이블의 RLS가
--   실제로 열람을 어디까지 허용하는지에 최종적으로 의존한다 -- 그 정책이 이미 폭넓게
--   열려 있다면(예: authenticated 전원이 runs를 읽을 수 있다면) 이 뷰만 고쳐도 근본
--   노출은 안 막힌다. 그 정책은 이 저장소에 없다(schema DDL은 upstream-only,
--   shared/config.js 참고) -- 라이브 Supabase에서 직접 확인 필요.
--   EXIT: 개인별 열람으로 좁혀야 한다는 결정이 나면 where 절에
--   `and r.member_id = auth.uid()`를 추가.
--
-- 🔴 이 파일을 고쳐도 이미 배포된 라이브 Supabase의 뷰는 바뀌지 않는다 -- 이 마이그레이션을
-- 대상 프로젝트에 다시 실행해야 실제로 반영된다.
create or replace view public.p04_timing_view
with (security_invoker = true)
as
select
  r.id as run_id,
  r.model,
  r.hint_mode,
  r.status,
  r.started_at,
  r.finished_at,
  extract(epoch from (r.finished_at - r.started_at)) * 1000 as wall_ms,
  r.timing_ms
from public.runs r
where r.pipeline = 'p04' and r.hint_mode is not null
order by r.started_at desc;

grant select on public.p04_timing_view to authenticated;

-- 🔴 같은 패턴(email 컬럼 노출 + security_invoker 미지정 definer 뷰) 후보가 이 저장소
-- 밖에 더 있다 -- p01_questions_view, p03_progress_view, p03_turns_view,
-- runs_with_email, artifacts_with_email (shared/config.js:22-23, shared/db.js:138에서
-- 언급됨). 이 DDL들은 이 저장소 어디에도 추적되지 않는다(라이브 Supabase 대시보드에서
-- 직접 만들어진 것으로 보임) -- 그래서 여기서 같이 고칠 수 없다. 라이브 프로젝트에서
-- 각 뷰 정의를 직접 조회해 같은 패턴인지 확인 필요.
