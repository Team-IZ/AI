""" 테스트용 가짜 엔진(스텁) """

from typing import Any

class StubAnalysisEngine:
    
    def analyze(
        self, request: dict[str, Any], zip_bytes: bytes | None = None
    ) -> dict[str, Any]:
        # 요청 값을 결과에 반영해 연결되었는지 확인
        # 스텁이 요청 무시하고 고정값만 주면 실제값 확인 불가
        applied_scope = request["extraction_scope"]
        byte_count = len(zip_bytes) if zip_bytes is not None else 1024
        
        return {
            "snapshot_id": "00000000-0000-0000-0000-000000000001",
            "snapshot_meta": {
                "content_hash": "0" * 64,  # sha256 hex 64자 자리
                "file_count": 3,
                "byte_count": byte_count,  # ZIP 실제 바이트 반영
            },
            "applied_scope": applied_scope,  # 요청 범위 그대로 에코
            "scope_fallback": False,
            "fallback_reason": None,
            "commit_sha": "0123456789abcdef0123456789abcdef01234567",
            # findings 각 항목은 DB decision_point 테이블 컬럼명을 쓴다.
            # (예전 findingId/sourcePath 같은 임의 이름 → 실제 컬럼명으로 교정)
            # type/reference_type 문자열 값은 카탈로그 미정(B-3)이라 잠정값.
            "findings": [
                {
                    "dp_id": "00000000-0000-0000-0000-0000000000dp",
                    "type": "CODE_RISK",        # 잠정값
                    "status": "OPEN",
                    "priority": 1,
                    "focus_code": "hardcoded-secret",
                    "source_path": "app/main.py",
                    "line_start": 12,
                    "line_end": 14,
                    "evidence_hash": "1" * 64,
                    "extractor_version": "stub-0",
                    "references": [
                        {
                            "path": "app/main.py",
                            "line_start": 12,
                            "line_end": 14,
                            "evidence_hash": "1" * 64,
                            "reference_type": "PRIMARY",  # 잠정값
                        }
                    ],
                }
            ],
            "question_count_planned": request["question_budget"],  # 요청 예산 반영
        }