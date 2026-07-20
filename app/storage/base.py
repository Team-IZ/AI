"""ResultStore 추상 인터페이스 (PLAN §1.5 구현 원칙).

확정 데이터(finding, transcript, 채점)의 영속화만 이 인터페이스를 거친다.
진행 중 세션의 휘발성 턴 상태는 모드와 무관하게 FastAPI 로컬 인메모리에 둔다.

- standalone 모드: supabase_store (Phase 5에서 구현) — Supabase가 Spring Boot의 대역.
- integrated 모드: null_store — 저장하지 않고 응답/콜백으로 Spring에 반환.
"""
from abc import ABC, abstractmethod
from typing import Any


class ResultStore(ABC):
    """확정 데이터 영속화 어댑터. 두 모드에서 API 스키마는 동일해야 한다."""

    @abstractmethod
    def save_findings(self, submission_id: str, findings: dict[str, Any]) -> None:
        """P02 분석 확정 결과(scan + judgment) 저장."""

    @abstractmethod
    def save_transcript(self, session_id: str, transcript: list[dict[str, Any]]) -> None:
        """P03 세션 종료 시 전사(transcript) 전체 저장."""

    @abstractmethod
    def save_grades(self, session_id: str, grades: dict[str, Any]) -> None:
        """세션 종료 후 5축 후채점 결과(총 25점) 저장."""
