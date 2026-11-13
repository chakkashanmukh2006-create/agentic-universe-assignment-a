"""
Data Quality tracking and reporting component.
Maintains and computes exact data quality metrics per assignment-a-data/README.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from solution.models import IngestionLog, InteractionEvent, Rejection


@dataclass
class FileQualityMetrics:
    """Quality metrics collected while processing a single file."""
    lead_rows_total: int = 0
    lead_rows_rejected: int = 0
    rejections_by_reason: dict[str, int] = field(default_factory=dict)
    duplicate_lead_rows_merged: int = 0
    field_warnings: int = 0
    events_total: int = 0
    malformed_events: int = 0
    duplicate_events_dropped: int = 0
    orphan_events: int = 0
    unknown_disposition_events: int = 0

    def add_rejection(self, reason: str) -> None:
        self.lead_rows_rejected += 1
        self.rejections_by_reason[reason] = self.rejections_by_reason.get(reason, 0) + 1


class DataQualityManager:
    """
    Computes overall data quality metrics across all ingested drops.
    Orphan counts dynamically reflect the final linked state.
    """

    @classmethod
    def get_summary(cls, session: Session) -> dict[str, Any]:
        """
        Aggregate final data quality metrics from the database.
        """
        # Sum file-level metrics from distinct ingested files
        logs = session.query(IngestionLog).all()

        lead_rows_total = sum(log.lead_rows_total for log in logs)
        duplicate_lead_rows_merged = sum(log.duplicate_lead_rows_merged for log in logs)
        field_warnings = sum(log.field_warnings for log in logs)
        events_total = sum(log.events_total for log in logs)
        malformed_events = sum(log.malformed_events for log in logs)
        duplicate_events_dropped = sum(log.duplicate_events_dropped for log in logs)
        unknown_disposition_events = sum(log.unknown_disposition_events for log in logs)

        # Rejections directly from the rejections table for absolute consistency
        lead_rows_rejected = session.query(Rejection).count()

        rejection_counts = (
            session.query(Rejection.reason, func.count(Rejection.id))
            .group_by(Rejection.reason)
            .all()
        )
        rejections_by_reason = {reason: count for reason, count in rejection_counts}

        # Orphan count reflects the FINAL state in the ledger
        orphan_events = (
            session.query(InteractionEvent)
            .filter(InteractionEvent.is_orphan.is_(True))
            .count()
        )

        return {
            "lead_rows_total": lead_rows_total,
            "lead_rows_rejected": lead_rows_rejected,
            "rejections_by_reason": rejections_by_reason,
            "duplicate_lead_rows_merged": duplicate_lead_rows_merged,
            "field_warnings": field_warnings,
            "events_total": events_total,
            "malformed_events": malformed_events,
            "duplicate_events_dropped": duplicate_events_dropped,
            "orphan_events": orphan_events,
            "unknown_disposition_events": unknown_disposition_events,
        }

    @classmethod
    def write_json(cls, session: Session, out_path: Path) -> None:
        """Write the data quality summary to the given path as JSON."""
        summary = cls.get_summary(session)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, mode="w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

