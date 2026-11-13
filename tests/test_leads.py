"""
Unit and integration tests for lead ingestion.
Tests header aliases, schema order rejection, missing required, blank rows,
field warnings, duplicate merging, and idempotency.
"""
from pathlib import Path
import tempfile
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from solution.leads import ingest_lead_file
from solution.models import Base, Lead, Rejection
from solution.security import decrypt_pii, deterministic_phone_hash


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_lead_ingestion_basic(db_session, tmp_path):
    csv_content = '''customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language
Aarav Menon,+919876543210,MOT/2026/100001,2026-11-23,"₹27,554",mr
'''
    f = tmp_path / "batch_001.csv"
    f.write_text(csv_content, encoding="utf-8")

    metrics = ingest_lead_file(f, "motor_renewal", db_session)

    assert metrics.lead_rows_total == 1
    assert metrics.lead_rows_rejected == 0
    assert metrics.duplicate_lead_rows_merged == 0
    assert metrics.field_warnings == 0

    lead = db_session.query(Lead).first()
    assert lead is not None
    assert lead.workflow_id == "motor_renewal"
    assert lead.batch_id == "batch_001"
    assert lead.external_ref == "MOT/2026/100001"
    assert lead.preferred_language == "mr"
    assert lead.premium_amount == 27554.0
    assert lead.phone_masked == "+91******3210"
    assert decrypt_pii(lead.customer_name_encrypted) == "Aarav Menon"
    assert decrypt_pii(lead.phone_encrypted) == "+919876543210"


def test_lead_header_aliases_and_extra_columns(db_session, tmp_path):
    csv_content = """Language,Insured Name,Policy No,Mobile No,Expiry Date,Premium,agent_notes
hi,Pooja Sharma,MOT/2026/100002,91-88803-95512,14-10-2026,Rs. 24787,called before
"""
    f = tmp_path / "batch_002.csv"
    f.write_text(csv_content, encoding="utf-8")

    metrics = ingest_lead_file(f, "motor_renewal", db_session)
    assert metrics.lead_rows_total == 1
    assert metrics.lead_rows_rejected == 0

    lead = db_session.query(Lead).first()
    assert lead is not None
    assert decrypt_pii(lead.customer_name_encrypted) == "Pooja Sharma"
    assert lead.external_ref == "MOT/2026/100002"
    assert lead.premium_amount == 24787.0
    assert lead.preferred_language == "hi"


def test_lead_blank_rows_skipped(db_session, tmp_path):
    csv_content = """,,,,,
customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language
   ,   ,   ,   ,   ,   
Sneha Pillai,06832582386,MOT/2026/100003,2026-10-14,Rs. 24231,en
,,,,,
"""
    f = tmp_path / "batch_001.csv"
    f.write_text(csv_content, encoding="utf-8")

    metrics = ingest_lead_file(f, "motor_renewal", db_session)
    assert metrics.lead_rows_total == 1
    assert metrics.lead_rows_rejected == 0

    rejections = db_session.query(Rejection).all()
    assert len(rejections) == 0


def test_lead_schema_order_rejections(db_session, tmp_path):
    csv_content = """customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language
,9876543210,MOT/2026/100004,2026-10-14,10000,en
Rahul Sharma,5123456789,MOT/2026/100005,2026-10-14,10000,en
Amit Verma,,MOT/2026/100006,2026-10-14,10000,en
Karthik Rao,9876543211,,2026-10-14,10000,en
"""
    f = tmp_path / "batch_001.csv"
    f.write_text(csv_content, encoding="utf-8")

    metrics = ingest_lead_file(f, "motor_renewal", db_session)
    assert metrics.lead_rows_total == 4
    assert metrics.lead_rows_rejected == 4

    rejections = db_session.query(Rejection).order_by(Rejection.row_number).all()
    assert len(rejections) == 4
    assert rejections[0].row_number == 1
    assert rejections[0].reason == "missing_required:customer_name"

    assert rejections[1].row_number == 2
    assert rejections[1].reason == "invalid_phone"

    assert rejections[2].row_number == 3
    assert rejections[2].reason == "missing_required:phone"

    assert rejections[3].row_number == 4
    assert rejections[3].reason == "missing_required:policy_number"


def test_lead_optional_field_warnings(db_session, tmp_path):
    csv_content = """customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language
Diya Menon,9876543210,MOT/2026/100007,31/02/2026,twelve thousand,Hindi
"""
    f = tmp_path / "batch_001.csv"
    f.write_text(csv_content, encoding="utf-8")

    metrics = ingest_lead_file(f, "motor_renewal", db_session)
    assert metrics.lead_rows_total == 1
    assert metrics.lead_rows_rejected == 0
    # 3 invalid optional fields: expiry date, premium, language
    assert metrics.field_warnings == 3

    lead = db_session.query(Lead).first()
    assert lead.policy_expiry is None
    assert lead.premium_amount is None
    assert lead.preferred_language == "en"  # Default applied


def test_lead_duplicate_merging_and_batch_id_preservation(db_session, tmp_path):
    csv1 = """customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language
Aarav Sharma,9876543210,MOT/2026/100008,2026-10-14,10000,hi
"""
    f1 = tmp_path / "batch_001.csv"
    f1.write_text(csv1, encoding="utf-8")
    ingest_lead_file(f1, "motor_renewal", db_session)

    lead = db_session.query(Lead).first()
    assert lead.batch_id == "batch_001"
    assert lead.premium_amount == 10000.0
    assert lead.preferred_language == "hi"

    # Second file with same lead, updated premium and language
    csv2 = """customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language
Aarav Sharma,9876543210,MOT/2026/100008,,15000,ta
"""
    f2 = tmp_path / "batch_002.csv"
    f2.write_text(csv2, encoding="utf-8")
    metrics2 = ingest_lead_file(f2, "motor_renewal", db_session)

    assert metrics2.duplicate_lead_rows_merged == 1

    leads = db_session.query(Lead).all()
    assert len(leads) == 1
    updated_lead = leads[0]
    # batch_id must remain batch_001 permanently
    assert updated_lead.batch_id == "batch_001"
    # non-null fields updated
    assert updated_lead.premium_amount == 15000.0
    assert updated_lead.preferred_language == "ta"
    # null field in csv2 does not overwrite previous non-null value
    assert str(updated_lead.policy_expiry) == "2026-10-14"
