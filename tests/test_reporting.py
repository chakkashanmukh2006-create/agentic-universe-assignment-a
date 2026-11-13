"""
Unit and integration tests for report generation and business metrics.
Tests bucket logic, effective disposition, connectivity rate precision,
IST date formatting, sorting, and output files.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from solution.config import SCHEMAS_DIR
from solution.models import Base, DispositionMetadata, InteractionEvent, Lead
from solution.reporting import compute_call_facts, compute_lead_bucket, generate_reports


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        # Load dispositions
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


def test_bucket_logic():
    # 1. No calls -> yet_to_reach_out
    assert compute_lead_bucket([]) == "yet_to_reach_out"

    # Mock call fact
    from solution.reporting import CallFact

    # 2. Terminal disposition -> completed
    c_term = CallFact(
        call_id="c1", workflow_id="motor_renewal", lead_id=1,
        initiated_at=datetime.now(timezone.utc), ist_date=datetime.now(timezone.utc).date(),
        is_connected=True, has_telephony_failed=False, effective_disposition="renewed",
        is_terminal=True, is_success=True
    )
    assert compute_lead_bucket([c_term]) == "completed"

    # 3. Telephony failed for all calls -> in_progress_telephony_failed
    c_fail = CallFact(
        call_id="c2", workflow_id="motor_renewal", lead_id=1,
        initiated_at=datetime.now(timezone.utc), ist_date=datetime.now(timezone.utc).date(),
        is_connected=False, has_telephony_failed=True, effective_disposition=None,
        is_terminal=False, is_success=False
    )
    assert compute_lead_bucket([c_fail]) == "in_progress_telephony_failed"

    # 4. Dialed (non-terminal, non-failed) -> in_progress_dialed
    c_dial = CallFact(
        call_id="c3", workflow_id="motor_renewal", lead_id=1,
        initiated_at=datetime.now(timezone.utc), ist_date=datetime.now(timezone.utc).date(),
        is_connected=True, has_telephony_failed=False, effective_disposition="callback_requested",
        is_terminal=False, is_success=False
    )
    assert compute_lead_bucket([c_dial]) == "in_progress_dialed"

    # Telephony failed + dialed -> in_progress_dialed (since not ALL failed)
    assert compute_lead_bucket([c_fail, c_dial]) == "in_progress_dialed"

    # Completed overrides telephony failed
    assert compute_lead_bucket([c_fail, c_term]) == "completed"


def test_generate_all_reports(db_session, tmp_path):
    # Setup test lead
    lead = Lead(
        workflow_id="motor_renewal",
        phone_hash="hash_001",
        phone_masked="+91******1234",
        phone_encrypted="enc_phone",
        customer_name_encrypted="enc_name",
        external_ref="MOT/2026/000001",
        batch_id="batch_001",
        preferred_language="en",
    )
    db_session.add(lead)
    db_session.flush()

    # Add call events on 2026-10-01 (10:00 UTC = 15:30 IST)
    t0 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    ev1 = InteractionEvent(
        event_id="e1", event_type="call.initiated", workflow_id="motor_renewal",
        call_id="call_1", occurred_at=t0, phone_hash="hash_001", lead_id=lead.id,
        is_orphan=False, payload_json="{}"
    )
    ev2 = InteractionEvent(
        event_id="e2", event_type="call.connected", workflow_id="motor_renewal",
        call_id="call_1", occurred_at=t0 + timedelta(seconds=10), phone_hash="hash_001", lead_id=lead.id,
        is_orphan=False, payload_json="{}"
    )
    ev3 = InteractionEvent(
        event_id="e3", event_type="disposition.set", workflow_id="motor_renewal",
        call_id="call_1", occurred_at=t0 + timedelta(seconds=60), phone_hash="hash_001", lead_id=lead.id,
        is_orphan=False, disposition_code="callback_requested", is_valid_disposition=True, payload_json="{}"
    )
    # Supervisor overrides to renewed
    ev4 = InteractionEvent(
        event_id="e4", event_type="disposition.override", workflow_id="motor_renewal",
        call_id="call_1", occurred_at=t0 + timedelta(hours=1), phone_hash="hash_001", lead_id=lead.id,
        is_orphan=False, disposition_code="renewed", is_valid_disposition=True, payload_json="{}"
    )
    db_session.add_all([ev1, ev2, ev3, ev4])
    db_session.commit()

    out_dir = tmp_path / "reports"
    report_paths = generate_reports(db_session, out_dir)

    # 1. batch_summary.csv
    assert report_paths["batch_summary"].exists()
    with open(report_paths["batch_summary"]) as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["workflow_id"] == "motor_renewal"
        assert rows[0]["batch_id"] == "batch_001"
        assert rows[0]["total_leads"] == "1"
        assert rows[0]["completed"] == "1"

    # 2. daily_overview.csv
    assert report_paths["daily_overview"].exists()
    with open(report_paths["daily_overview"]) as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["workflow_id"] == "motor_renewal"
        assert rows[0]["date"] == "2026-10-01"
        assert rows[0]["total_calls"] == "1"
        assert rows[0]["connected_calls"] == "1"
        assert rows[0]["connectivity_rate"] == "1.0000"
        assert rows[0]["success_leads"] == "1"

    # 3. lead_report.csv
    assert report_paths["lead_report"].exists()
    with open(report_paths["lead_report"]) as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["external_ref"] == "MOT/2026/000001"
        assert rows[0]["phone_masked"] == "+91******1234"
        assert rows[0]["bucket"] == "completed"
        assert rows[0]["total_calls"] == "1"
        assert rows[0]["connected_calls"] == "1"
        assert rows[0]["last_call_at"] == "2026-10-01T15:30:00+05:30"
        assert rows[0]["current_disposition"] == "renewed"
        assert rows[0]["ever_success"] == "true"

    # 4. rejections.csv
    assert report_paths["rejections"].exists()

    # 5. data_quality.json
    assert report_paths["data_quality"].exists()
    with open(report_paths["data_quality"]) as f:
        dq = json.load(f)
        assert "lead_rows_total" in dq
        assert "orphan_events" in dq
        assert dq["orphan_events"] == 0

