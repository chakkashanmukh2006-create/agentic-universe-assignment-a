"""
CLI interface for the Interaction Ledger & Reporting Pipeline.
Supports commands:
  python -m solution.main ingest <drop_path>
  python -m solution.main report --out <output_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console

from solution.config import DATABASE_URL
from solution.database import get_session, init_db
from solution.events import ingest_event_file, resolve_orphans
from solution.leads import ingest_lead_file
from solution.reporting import generate_reports

app = typer.Typer(
    name="au-pipeline",
    help="Agentic Universe Interaction Ledger & Reporting Pipeline",
    add_completion=False,
)
console = Console()


@app.command()
def ingest(
    drop_path: Path = typer.Argument(
        ...,
        help="Path to the drop directory (e.g. data/drop_1)",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    db: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database URL override (defaults to DATABASE_URL env or SQLite)",
    ),
) -> None:
    """
    Ingest a data drop containing leads CSVs and events JSONL files.
    Processing is idempotent and append-only.
    """
    db_url = db or DATABASE_URL
    console.print(f"[bold blue]Initializing database:[/bold blue] {db_url}")
    init_db(db_url)

    leads_dir = drop_path / "leads"
    events_dir = drop_path / "events"

    with get_session(db_url) as session:
        # 1. Ingest Leads
        total_leads_processed = 0
        total_leads_rejected = 0
        total_leads_merged = 0

        if leads_dir.exists() and leads_dir.is_dir():
            # Process workflows in sorted order
            workflow_dirs = sorted([d for d in leads_dir.iterdir() if d.is_dir()])
            for wf_dir in workflow_dirs:
                wf_id = wf_dir.name
                csv_files = sorted(wf_dir.glob("*.csv"))
                for csv_file in csv_files:
                    console.print(f"  Ingesting leads: [green]{csv_file}[/green] ({wf_id})")
                    m = ingest_lead_file(csv_file, wf_id, session)
                    total_leads_processed += m.lead_rows_total
                    total_leads_rejected += m.lead_rows_rejected
                    total_leads_merged += m.duplicate_lead_rows_merged

        # 2. Ingest Events
        total_events_processed = 0
        total_events_dropped = 0
        total_events_malformed = 0

        if events_dir.exists() and events_dir.is_dir():
            event_files = sorted(events_dir.glob("*.jsonl"))
            for ev_file in event_files:
                console.print(f"  Ingesting events: [green]{ev_file}[/green]")
                m = ingest_event_file(ev_file, session)
                total_events_processed += m.events_total
                total_events_dropped += m.duplicate_events_dropped
                total_events_malformed += m.malformed_events

        # 3. Final orphan reconciliation
        resolved = resolve_orphans(session)
        if resolved > 0:
            console.print(f"  [cyan]Resolved {resolved} previously orphaned interaction events.[/cyan]")

        console.print(f"[bold green]Ingestion complete for {drop_path.name}:[/bold green]")
        console.print(f"  Leads: {total_leads_processed} total, {total_leads_merged} merged duplicates, {total_leads_rejected} rejected.")
        console.print(f"  Events: {total_events_processed} total, {total_events_dropped} duplicate dropped, {total_events_malformed} malformed.")


@app.command()
def report(
    out: Path = typer.Option(
        Path("reports"),
        "--out",
        "-o",
        help="Directory to write report files to",
    ),
    db: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database URL override (defaults to DATABASE_URL env or SQLite)",
    ),
) -> None:
    """
    Generate the five exact business report files:
      1. batch_summary.csv
      2. daily_overview.csv
      3. lead_report.csv
      4. rejections.csv
      5. data_quality.json
    """
    db_url = db or DATABASE_URL
    init_db(db_url)

    console.print(f"[bold blue]Generating reports from database into:[/bold blue] {out}")
    with get_session(db_url) as session:
        paths = generate_reports(session, out)

    for name, p in paths.items():
        console.print(f"  [green]OK[/green] {name} -> {p}")

    console.print("[bold green]All 5 reports generated successfully![/bold green]")


@app.command()
def reset(
    db: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database URL override (defaults to DATABASE_URL env or SQLite)",
    ),
) -> None:
    """Reset database (drops and recreates all tables)."""
    db_url = db or DATABASE_URL
    from solution.models import Base
    from solution.database import get_engine
    engine = get_engine(db_url)
    Base.metadata.drop_all(engine)
    init_db(db_url)
    console.print("[bold yellow]Database reset complete.[/bold yellow]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

