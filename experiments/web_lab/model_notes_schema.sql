-- D1 (2026-07-24): 사용자 요청 -- curriculum-manager의 모델 선택 UI 아래 "비고" 텍스트가
-- 지금은 docs/lab/lab-core.js의 CURATED_MODELS에 하드코딩돼 있어 코드를 고쳐야만 바뀌고,
-- 팀원이 실사용 중 발견한 내용을 남길 방법이 없었음 -- 모든 팀원이 함께 읽고 쓸 수 있는
-- 공유 메모장으로 전환.
--
-- public 스키마에 둠(pdf_analysis 아님) -- 이 세션에서 이미 한 번 겪은 함정
-- (pdf_analysis.runs -> public.members 크로스스키마 embed가 PostgREST에서 400남)을
-- 피하려는 목적. 개념적으로도 "모델에 대한 메모"는 P01 전용이 아니라 이 lab 전체가
-- 나중에 같이 쓸 수 있는 성격.
--
-- Already applied live (2026-07-24, via Supabase Management API against project
-- oziaeqcvrkrqkhwrybfj) -- this file exists for reproducibility/reference, same
-- convention as pdf_analysis_schema.sql. Safe to re-run (IF NOT EXISTS guard) except the
-- RLS policies, which use CREATE POLICY without IF NOT EXISTS -- drop them first if
-- re-applying after editing a policy.
create table if not exists public.model_notes (
  model_id text primary key,
  note text not null default '',
  updated_by uuid references public.members(id),
  updated_at timestamptz not null default now()
);
alter table public.model_notes enable row level security;

-- D2: "아무 팀원이나 아무 행이나 쓸 수 있음" -- 이 프로젝트의 다른 테이블들(runs/artifacts)
-- 전부 "own만 write"인 것과 의도적으로 다른 정책. 협업 메모장이 목적이라 own-only로는
-- 다른 사람이 이미 남긴 메모를 못 고침.
--   WHY: 이 프로젝트에 처음 등장하는 "회원이면 아무 행이나 upsert 가능" 정책이라
--   명시적으로 남김 -- 나중에 "왜 이렇게 열려있지" 하는 의문에 이 커밋/파일이 답.
--   COST: 마지막에 쓴 사람이 이긴다(동시 편집 충돌 감지 없음), 수정 이력 없음.
--   EXIT: 분쟁/롤백이 실제 문제가 되면 model_notes_history(model_id, note, updated_by,
--   updated_at) 감사테이블을 UPDATE 트리거로 추가 -- 지금은 팀 규모(7명) 대비
--   과설계로 판단해 생략.
create policy "model_notes read all" on public.model_notes for select to authenticated using (true);
create policy "model_notes upsert any" on public.model_notes for insert to authenticated with check (true);
create policy "model_notes update any" on public.model_notes for update to authenticated using (true);

-- read/insert/update 전부 to authenticated로 한정 -- 비로그인 사용자는 기존처럼 정적
-- fallback 비고만 봄(새 기능은 로그인 게이트 뒤에만).
grant select, insert, update on public.model_notes to authenticated;
