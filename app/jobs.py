""" 분석 job의 인메모리 저장소와 수명주기.

프로세스 재시작 시 유실, 워커 1개에서만 동작
영속/스케일 필요시 Redis 나 DB로 이전, 지금은 단일 프로세스 개발용
"""

import json
import sys
import uuid

from datetime import datetime, timezone
from pathlib import Path

from app.engines.base import AnalysisEngine
from app.schemas.analysis import AnalysisJobStatus, AnalysisRequest, AnalysisResult
from app.schemas.usage import AiUsage

# D-pr3 (2026-07-31): PARALLEL_RUN_CHECKLIST.md PR-3(FastAPI 경로 지연·실패율 실측)의
# 로깅 지점. job이 터미널 상태가 될 때 딱 한 번(run_analysis의 finally, 폴링 호출
# 횟수와 무관) 측정 한 줄을 남긴다.
#   WHY 두 곳에 쓰는가(stdout 무조건 + 로컬 파일 best-effort): 이 서비스는 Dockerfile이
#     `app`과 `openapi.json`만 이미지에 COPY한다 -- `docs/`는 배포된 컨테이너 안에
#     존재하지 않는다. 로컬 파일 append만 구현하면 실제 운영 트래픽(팀 테스트
#     페이지가 치는 배포된 백엔드)에서는 그 경로 자체가 없어 조용히 아무 데이터도
#     안 쌓인다 -- PR-3이 요구하는 "실제 운영 세션 실측"을 정작 놓치게 된다.
#     stdout은 로컬/컨테이너 어디서나 항상 동작하고, Cloudflare Container도
#     stdout을 로그로 잡아준다(`wrangler tail`로 확인 가능) -- 그래서 배포본에서는
#     당분간 "wrangler tail로 긁어서 수동으로 jsonl에 합치기"가 실제 수집 경로다.
#     로컬 파일 append는 로컬 개발 중 편의를 위한 부가 경로일 뿐, 유일한 경로가
#     아니다.
#   COST: 배포본 쪽 실측은 완전 자동이 아니다(수동 하베스트 필요) -- 이 서비스에
#     DB를 새로 들이면 D1("FastAPI는 DB를 갖지 않는다, Spring이 영속화 담당")이
#     깨지므로 그 COST를 감수한다.
#   EXIT: 실제 메트릭 저장소(D1을 재검토해서든, 별도 사이드카든)가 생기면 stdout
#     프린트를 그 저장소 호출로 교체 -- 이 함수의 반환 타입(dict)은 그대로 재사용
#     가능하다.
_MEASUREMENTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "code-importance-map" / "measurements"


def _log_measurement(job: AnalysisJobStatus) -> None:
    """ job이 터미널 상태(SUCCEEDED/FAILED/PARTIAL)가 될 때 run_analysis의 finally가 호출.
    측정 자체의 실패가 분석 job 결과에 영향을 주면 안 된다(D6과 같은 원칙) --
    이 함수는 무엇을 하든 예외를 밖으로 던지지 않는다.

    D-pr3b (2026-08-03, 로깅 갭 수정): 원래는 job 전체의 latency_ms를 평평한
    리스트로만 남겨서, "이 job에 3콜이 있었다"는 알아도 그중 CODE_MAP이 실패했는지
    DIAGRAM이 실패했는지(D13 이후 CODE_MAP이 job 실패 없이도 개별 FAILED일 수 있음,
    D6 강등 철학) 스테이지별로 분해할 수 없었다(D10 재논의 시 필요하다고 지적됨).
      WHY: job.ai_usage의 각 AiUsage 항목이 이미 source_type/status/failure_code를
      갖고 있다(app/schemas/usage.py) -- 새로 계산할 게 없고 그대로 옮겨 담기만 하면 된다.
      COST: 기존에 이 파일 하나뿐인 잠재 소비자(PR-3 집계 스크립트, 아직 안 만들어짐)가
      있었다면 latency_ms 평면 리스트를 기대했을 텐데, 그 스크립트가 없으므로 스키마를
      깨는 실질적 비용은 0이다(measurements/ 안에 실제 .jsonl 데이터 자체가 아직 없음,
      확인됨).
      EXIT: 다시 평면 리스트가 필요해지면 [c["latency_ms"] for c in record["calls"]]로
      바로 유도 가능 -- 정보가 상위집합이라 되돌리기 쉬운 방향으로 바꿨다.
    """
    record = {
        "job_id": job.job_id,
        "status": job.status,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "failure_reason": job.failure_reason,
        "calls": [
            {
                "source_type": u.source_type,
                "status": u.status,
                "failure_code": u.failure_code,
                "latency_ms": u.latency_ms,
            }
            for u in job.ai_usage
        ],
    }
    line = json.dumps(record, ensure_ascii=False)

    # 1) stdout -- 항상, 로컬/컨테이너 무관하게 동작(D-pr3 WHY 참고)
    try:
        print(f"[pr3-measurement] {line}", file=sys.stdout, flush=True)
    except Exception:
        pass  # stdout 자체가 막힌 극단적 상황에서도 job 완료엔 영향 없어야 함

    # 2) 로컬 파일 -- docs/가 실제로 존재하는 로컬 개발 환경에서만 성공(그게 정상)
    try:
        date_str = (job.completed_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        _MEASUREMENTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(_MEASUREMENTS_DIR / f"{date_str}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 컨테이너처럼 docs/가 없는 환경 -- stdout 경로만으로 충분(D-pr3 EXIT)


# job_id
_jobs: dict[str, AnalysisJobStatus] = {}

# 멱등성 키 -> job_id. 같은 키 재요청 시 새 job 안 만들고 처음 id 반환
_job_id_by_idempotency_key: dict[str, str] = {}

def get_job(job_id: str) -> AnalysisJobStatus | None:
    return _jobs.get(job_id)

def job_id_for_key(idempotency_key: str) -> str | None:
    return _job_id_by_idempotency_key.get(idempotency_key)

def create_job(body: AnalysisRequest, idempotency_key: str | None) -> AnalysisJobStatus:
    """ QUEUED 상태 job을 만들어 저장. 아직 분석 X -> result 없음 """
    job = AnalysisJobStatus(
        job_id=str(uuid.uuid4()),
        attempt_id=body.attempt_id,
        submission_id=body.submission_id,
        status="QUEUED",
    )
    
    _jobs[job.job_id] = job
    if idempotency_key:
        _job_id_by_idempotency_key[idempotency_key] = job.job_id
    return job

def run_analysis(
    job_id: str, body: AnalysisRequest, engine: AnalysisEngine, zip_bytes: bytes | None
) -> None:
    """ 백그라운드 워커. 상태 전이시키며 분석 수행 
    
    QUEUED -> RUNNING -> SUCCEEDED (엔진 터지면 FAILED)
    202 응답 나간 뒤 실행, 호출자는 폴링(GET)으로 이 전이 관측.
    
    _jobs에 담긴 객체를 그 자리에서 수정 -> GET이 같은 객체 돌려주므로 변경 그대로 보임
    """
    job = _jobs[job_id]
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)
    
    try:
        raw = engine.analyze(body.model_dump(), zip_bytes)
        # D-timing (2026-07-30): ai_usage는 AnalysisResult 필드가 아니라 엔진이
        # 얹어 보내는 형제 키다(app/engines/base.py 참고) -- 먼저 꺼내야
        # AnalysisResult.model_validate가 나머지만 보고 계약 위반을 정확히 잡는다.
        usage_raw = raw.pop("ai_usage", [])
        result = AnalysisResult.model_validate(raw)  # 계약 위반은 여기서 예외

        # 요구사항 판정은 빠짐없이 와야 한다. 모델이 몇 개를 조용히 빠뜨리면
        # 판정 안 된 요구사항이 통과로 기록된다 — 여기서 막는다.
        if len(result.requirement_results) != len(body.requirements):
            raise ValueError(
                f"requirementResults 개수가 요청 requirements와 다릅니다: "
                f"{len(result.requirement_results)} != {len(body.requirements)}"
            )

        job.result = result
        job.ai_usage = [AiUsage.model_validate(u) for u in usage_raw]
        job.status = "SUCCEEDED"
    except Exception as exc:
        # 엔진 터지거나 계약 어기면 job FAILED로. 예외 삼키지 말고 사유 기록
        job.status = "FAILED"
        job.failure_reason = str(exc)
    finally:
        job.completed_at = datetime.now(timezone.utc)
        # D-pr3: 터미널 전이 시 정확히 한 번. _log_measurement 자신이 이미 모든 내부
        # 에러를 삼키지만, finally 블록에서 새 예외가 나가면 원래 결과(SUCCEEDED/FAILED)를
        # 덮어써버리므로 호출부에도 벨트-앤-브레이스를 둔다(crew.py D12와 같은 원칙).
        try:
            _log_measurement(job)
        except Exception:
            pass