"""
Unit and integration tests for event ingestion and orphan resolution.
Tests event deduplication, malformed JSON lines, orphan linking,
late-arriving leads, supervisor overrides without phone, unknown dispositions,
and append-only ledger preservation.
"""
from pathlib import Path
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from solution.database import load_disposition_catalogue
from solution.events import ingest_event_file, resolve_orphans
from solution.leads import ingest_lead_file
from solution.models import Base, InteractionEvent, Lead
from solution.security import deterministic_phone_hash


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        # Copy disposition metadata to test session
        from solution.config import SCHEMAS_DIR
        import csv
        from solution.models import DispositionMetadata
        with open(SCHEMAS_DIR / "dispositions.csv", mode="r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                session.merge(DispositionMetadata(
                    workflow_id=row["workflow_id"].strip(),
                    code=row["code"].strip(),
                    label=row["label"].strip(),
                    is_terminal=row["is_terminal"].strip().lower() == "true",
                    is_success=row["is_success"].strip().lower() == "true",
                ))
        session.commit()
        yield session
    finally:
        session.close()


def test_malformed_and_duplicate_events(db_session, tmp_path):
    events_jsonl = """{"event_id": "evt_001", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "9876543210"}
this is malformed json
{"not_an_event_id": "abc"}
{"event_id": "", "event_type": "call.ended"}
{"event_id": "evt_001", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:01:00Z", "phone": "9876543210"}
"""
    f = tmp_path / "events_0001.jsonl"
    f.write_text(events_jsonl, encoding="utf-8")

    metrics = ingest_event_file(f, db_session)
    assert metrics.events_total == 5
    assert metrics.malformed_events == 3
    assert metrics.duplicate_events_dropped == 1

    stored = db_session.query(InteractionEvent).all()
    assert len(stored) == 1
    assert stored[0].event_id == "evt_001"


def test_event_linking_to_existing_lead(db_session, tmp_path):
    # 1. Ingest lead
    lead_csv = """customer_name,phone,policy_number
Aarav Sharma,9876543210,MOT/2026/001
"""
    lead_file = tmp_path / "batch_001.csv"
    lead_file.write_text(lead_csv, encoding="utf-8")
    ingest_lead_file(lead_file, "motor_renewal", db_session)

    lead = db_session.query(Lead).first()
    assert lead is not None

    # 2. Ingest event with unformatted phone
    events_jsonl = """{"event_id": "evt_002", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c2", "occurred_at": "2026-10-02T10:00:00Z", "phone": "+91 98765 43210"}
"""
    ev_file = tmp_path / "events_0002.jsonl"
    ev_file.write_text(events_jsonl, encoding="utf-8")
    metrics = ingest_event_file(ev_file, db_session)

    assert metrics.events_total == 1
    event = db_session.query(InteractionEvent).filter_by(event_id="evt_002").first()
    assert event is not None
    assert event.lead_id == lead.id
    assert event.is_orphan is False


def test_orphan_resolution_when_lead_arrives_later(db_session, tmp_path):
    # 1. Event arrives first (orphan)
    events_jsonl = """{"event_id": "evt_003", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c3", "occurred_at": "2026-10-03T10:00:00Z", "phone": "91-98765-43210"}
"""
    ev_file = tmp_path / "events_0003.jsonl"
    ev_file.write_text(events_jsonl, encoding="utf-8")
    metrics = ingest_event_file(ev_file, db_session)

    event = db_session.query(InteractionEvent).filter_by(event_id="evt_003").first()
    assert event.lead_id is None
    assert event.is_orphan is True

    # 2. Lead arrives later (e.g. in drop 2)
    lead_csv = """customer_name,phone,policy_number
Kavya Bose,9876543210,MOT/2026/003
"""
    lead_file = tmp_path / "batch_003.csv"
    lead_file.write_text(lead_csv, encoding="utf-8")
    ingest_lead_file(lead_file, "motor_renewal", db_session)

    # Lead ingestion triggers resolve_orphans automatically
    db_session.refresh(event)
    lead = db_session.query(Lead).first()
    assert event.lead_id == lead.id
    assert event.is_orphan is False


def test_supervisor_override_links_via_call_id(db_session, tmp_path):
    # Ingest lead
    lead_csv = """customer_name,phone,policy_number
Rohan Sharma,9876543210,MOT/2026/004
"""
    lead_file = tmp_path / "batch_001.csv"
    lead_file.write_text(lead_csv, encoding="utf-8")
    ingest_lead_file(lead_file, "motor_renewal", db_session)

    # Ingest call events: call.initiated (has phone), disposition.set (has phone), disposition.override (NO phone)
    events_jsonl = """{"event_id": "evt_004", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-04T10:00:00Z", "phone": "9876543210"}
{"event_id": "evt_005", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-04T10:05:00Z", "phone": "9876543210", "payload": {"disposition_code": "payment_link_sent"}}
{"event_id": "evt_006", "event_type": "disposition.override", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-04T12:00:00Z", "payload": {"disposition_code": "renewed", "overridden_by": "supervisor_1", "reason": "qa_review"}}
"""
    ev_file = tmp_path / "events_0004.jsonl"
    ev_file.write_text(events_jsonl, encoding="utf-8")
    ingest_event_file(ev_file, db_session)

    override_ev = db_session.query(InteractionEvent).filter_by(event_id="evt_006").first()
    assert override_ev is not None
    assert override_ev.lead_id is not None
    assert override_ev.is_orphan is False
    assert override_ev.disposition_code == "renewed"

    # Verify append-only ledger: original disposition.set event was NOT modified or deleted
    set_ev = db_session.query(InteractionEvent).filter_by(event_id="evt_005").first()
    assert set_ev is not None
    assert set_ev.disposition_code == "payment_link_sent"


def test_unknown_disposition_code(db_session, tmp_path):
    events_jsonl = """{"event_id": "evt_007", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c5", "occurred_at": "2026-10-05T10:00:00Z", "phone": "9876543210", "payload": {"disposition_code": "INVALID_CODE_999"}}
"""
    ev_file = tmp_path / "events_0005.jsonl"
    ev_file.write_text(events_jsonl, encoding="utf-8")
    metrics = ingest_event_file(ev_file, db_session)

    assert metrics.unknown_disposition_events == 1
    ev = db_session.query(InteractionEvent).filter_by(event_id="evt_007").first()
    assert ev is not None
    assert ev.is_valid_disposition is False
