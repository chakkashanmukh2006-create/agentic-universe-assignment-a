"""
SQLAlchemy models for the Interaction Ledger & Reporting Pipeline.
Implements the append-only interaction ledger, PII protection, rejections,
and ingestion tracking.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey,
    Index, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Lead(Base):
    """
    Customer leads merged by (workflow_id, phone_hash).
    Contains encrypted PII and deterministic hashes for secure searching/joining.
    """
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(String(64), nullable=False, index=True)
    phone_hash = Column(String(64), nullable=False, index=True)
    phone_masked = Column(String(16), nullable=False)
    phone_encrypted = Column(Text, nullable=False)
    customer_name_encrypted = Column(Text, nullable=False)
    external_ref = Column(String(128), nullable=False, index=True)
    batch_id = Column(String(64), nullable=False, index=True)
    product = Column(String(128), nullable=True)
    policy_expiry = Column(Date, nullable=True)
    order_date = Column(Date, nullable=True)
    premium_amount = Column(Float, nullable=True)
    preferred_language = Column(String(16), nullable=False, default="en")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    events = relationship("InteractionEvent", back_populates="lead")

    __table_args__ = (
        UniqueConstraint("workflow_id", "phone_hash", name="uq_lead_workflow_phone_hash"),
        Index("ix_lead_workflow_external_ref", "workflow_id", "external_ref"),
        Index("ix_lead_workflow_batch", "workflow_id", "batch_id"),
    )


class InteractionEvent(Base):
    """
    Append-only interaction ledger recording every telephony event and disposition.
    Original events are immutable and never updated or deleted.
    """
    __tablename__ = "interaction_events"

    event_id = Column(String(64), primary_key=True)
    event_type = Column(String(64), nullable=False, index=True)
    workflow_id = Column(String(64), nullable=False, index=True)
    call_id = Column(String(64), nullable=False, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    phone_hash = Column(String(64), nullable=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    is_orphan = Column(Boolean, nullable=False, default=True, index=True)
    disposition_code = Column(String(64), nullable=True, index=True)
    is_valid_disposition = Column(Boolean, nullable=False, default=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    lead = relationship("Lead", back_populates="events")

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)

    __table_args__ = (
        Index("ix_event_call_occurred", "call_id", "occurred_at"),
        Index("ix_event_lead_type", "lead_id", "event_type"),
    )


class Rejection(Base):
    """
    Audit table of rejected lead rows. Contains zero PII.
    """
    __tablename__ = "rejections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(String(64), nullable=False, index=True)
    batch_id = Column(String(64), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    reason = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("workflow_id", "batch_id", "row_number", name="uq_rejection_row"),
        Index("ix_rejection_sort", "workflow_id", "batch_id", "row_number"),
    )


class IngestionLog(Base):
    """
    Tracks processed files and their metrics to ensure idempotency.
    """
    __tablename__ = "ingestion_history"

    file_path = Column(String(255), primary_key=True)
    file_type = Column(String(32), nullable=False)  # "lead" or "event"
    workflow_id = Column(String(64), nullable=True)
    batch_id = Column(String(64), nullable=True)
    lead_rows_total = Column(Integer, default=0, nullable=False)
    lead_rows_rejected = Column(Integer, default=0, nullable=False)
    duplicate_lead_rows_merged = Column(Integer, default=0, nullable=False)
    field_warnings = Column(Integer, default=0, nullable=False)
    events_total = Column(Integer, default=0, nullable=False)
    malformed_events = Column(Integer, default=0, nullable=False)
    duplicate_events_dropped = Column(Integer, default=0, nullable=False)
    orphan_events = Column(Integer, default=0, nullable=False)
    unknown_disposition_events = Column(Integer, default=0, nullable=False)
    ingested_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class DispositionMetadata(Base):
    """
    Catalogue of valid dispositions per workflow loaded from dispositions.csv.
    """
    __tablename__ = "disposition_catalogue"

    workflow_id = Column(String(64), nullable=False)
    code = Column(String(64), nullable=False)
    label = Column(String(128), nullable=False)
    is_terminal = Column(Boolean, nullable=False)
    is_success = Column(Boolean, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("workflow_id", "code"),
    )

