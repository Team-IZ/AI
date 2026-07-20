"""integrated 모드용 ResultStore: 아무것도 저장하지 않는다 (PLAN §1.5 모드 B).

DB 단일 소유자는 Spring Boot이므로 FastAPI는 확정 데이터를 응답(또는 완료 콜백)으로
반환할 뿐 영속화하지 않는다. 여기서는 디버깅을 위한 로그만 남긴다.
"""
import logging
from typing import Any

from app.storage.base import ResultStore

logger = logging.getLogger(__name__)


class NullStore(ResultStore):
    def save_findings(self, submission_id: str, findings: dict[str, Any]) -> None:
        logger.info(
            "NullStore.save_findings: submission=%s (integrated mode, not persisted)",
            submission_id,
        )

    def save_transcript(self, session_id: str, transcript: list[dict[str, Any]]) -> None:
        logger.info(
            "NullStore.save_transcript: session=%s turns=%d (integrated mode, not persisted)",
            session_id,
            len(transcript),
        )

    def save_grades(self, session_id: str, grades: dict[str, Any]) -> None:
        logger.info(
            "NullStore.save_grades: session=%s (integrated mode, not persisted)",
            session_id,
        )
