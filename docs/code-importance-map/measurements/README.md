# PR-3 실측 로그

`PARALLEL_RUN_CHECKLIST.md`의 PR-3(FastAPI 경로가 운영 세션 기준 충족) 실측
절차가 쓰는 디렉터리. 파일럿 기간 동안 `GET /analyses/{jobId}`의 최종 폴링
응답마다 한 줄씩 append한다.

파일명: `<날짜:YYYY-MM-DD>.jsonl`

한 줄의 모양(예시):
```json
{"job_id": "...", "status": "SUCCEEDED", "started_at": "...", "completed_at": "...", "ai_usage_latency_ms": [1234, 567], "failure_reason": null}
```

20건이 쌓이면 p99 지연과 FAILED 비율을 계산해 `PARALLEL_RUN_CHECKLIST.md`의
X/Y를 갱신한다. 이 디렉터리 자체는 아직 비어 있다 -- 파일럿 시작 전이므로.
