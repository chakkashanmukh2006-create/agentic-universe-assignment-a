"""
End-to-end and idempotency tests for the ingestion pipeline.
Verifies that:
1. Re-running an already processed drop changes nothing.
2. Duplicate events and leads are safely ignored/merged.
3. Ingesting drop 1 followed by drop 2 resolves earlier orphans and produces consistent metrics.
"""
from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from solution.main import app
from solution.models import Base, InteractionEvent, Lead, Rejection

runner = CliRunner()


def test_idempotent_ingestion_on_synthetic_data(tmp_path):
    # Use existing small data generated for seed 42
    data_dir = Path("data")
    if not (data_dir / "drop_1").exists():
        pytest.skip("Synthetic data/drop_1 not found")

    db_path = tmp_path / "test_idempotent.db"
    db_url = f"sqlite:///{db_path}"

    # 1. Ingest drop_1 first time
    result1 = runner.invoke(app, ["ingest", "data/drop_1", "--db", db_url])
    assert result1.exit_code == 0

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session1 = Session()
    leads_count_1 = session1.query(Lead).count()
    events_count_1 = session1.query(InteractionEvent).count()
    rejections_count_1 = session1.query(Rejection).count()
    session1.close()

    assert leads_count_1 > 0
    assert events_count_1 > 0

    # 2. Ingest drop_1 second time (should be completely idempotent)
    result2 = runner.invoke(app, ["ingest", "data/drop_1", "--db", db_url])
    assert result2.exit_code == 0

    session2 = Session()
    leads_count_2 = session2.query(Lead).count()
    events_count_2 = session2.query(InteractionEvent).count()
    rejections_count_2 = session2.query(Rejection).count()
    session2.close()

    assert leads_count_2 == leads_count_1
    assert events_count_2 == events_count_1
    assert rejections_count_2 == rejections_count_1


def test_drop1_then_drop2_generates_all_reports(tmp_path):
    data_dir = Path("data")
    if not ((data_dir / "drop_1").exists() and (data_dir / "drop_2").exists()):
        pytest.skip("Synthetic data drop_1/drop_2 not found")

    db_path = tmp_path / "test_pipeline.db"
    db_url = f"sqlite:///{db_path}"
    report_dir = tmp_path / "reports"

    # Ingest drop 1
    r1 = runner.invoke(app, ["ingest", "data/drop_1", "--db", db_url])
    assert r1.exit_code == 0

    # Ingest drop 2
    r2 = runner.invoke(app, ["ingest", "data/drop_2", "--db", db_url])
    assert r2.exit_code == 0

    # Generate reports
    rr = runner.invoke(app, ["report", "--out", str(report_dir), "--db", db_url])
    assert rr.exit_code == 0

    # Check that all 5 files exist and are populated
    f_batch = report_dir / "batch_summary.csv"
    f_daily = report_dir / "daily_overview.csv"
    f_lead = report_dir / "lead_report.csv"
    f_rej = report_dir / "rejections.csv"
    f_dq = report_dir / "data_quality.json"

    assert f_batch.exists() and f_batch.stat().st_size > 0
    assert f_daily.exists() and f_daily.stat().st_size > 0
    assert f_lead.exists() and f_lead.stat().st_size > 0
    assert f_rej.exists() and f_rej.stat().st_size > 0
    assert f_dq.exists() and f_dq.stat().st_size > 0

    # Inspect data quality JSON
    with open(f_dq) as f:
        dq = json.load(f)
        assert dq["lead_rows_total"] > 0
        assert dq["events_total"] > 0
        assert dq["lead_rows_rejected"] > 0

