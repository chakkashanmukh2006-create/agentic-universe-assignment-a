"""
Reporting engine for Agentic Universe Assignment A.
Generates all five required reports:
1. batch_summary.csv
2. daily_overview.csv
3. lead_report.csv
4. rejections.csv
5. data_quality.json
Adheres strictly to the output contract, sorting, precision, and business logic.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session

from solution.data_quality import DataQualityManager
from solution.events import resolve_orphans
from solution.models import (
    DispositionMetadata,
    InteractionEvent,
    Lead,
    Rejection,
)
from solution.normalizers import format_iso_ist, to_ist


@dataclass
class CallFact:
    call_id: str
    workflow_id: str
    lead_id: int
    initiated_at: datetime
    ist_date: date
    is_connected: bool
    has_telephony_failed: bool
    effective_disposition: str | None
    is_terminal: bool
    is_success: bool


def _load_disposition_catalogue(session: Session) -> dict[tuple[str, str], tuple[bool, bool]]:
    """Return map of (workflow_id, code) -> (is_terminal, is_success)."""
    rows = session.query(
        DispositionMetadata.workflow_id,
        DispositionMetadata.code,
        DispositionMetadata.is_terminal,
        DispositionMetadata.is_success,
    ).all()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def compute_call_facts(session: Session) -> list[CallFact]:
    """
    Compute call facts for all linked calls.
    A call is counted only if its call.initiated event is present and linked.
    """
    disp_cat = _load_disposition_catalogue(session)

    # 1. Fetch all linked events
    events = (
        session.query(InteractionEvent)
        .filter(
            InteractionEvent.is_orphan.is_(False),
            InteractionEvent.lead_id.isnot(None),
        )
        .order_by(InteractionEvent.occurred_at.asc())
        .all()
    )

    # Group events by call_id
    call_events_map: dict[str, list[InteractionEvent]] = defaultdict(list)
    for ev in events:
        call_events_map[ev.call_id].append(ev)

    call_facts: list[CallFact] = []

    for call_id, ev_list in call_events_map.items():
        # Check if call.initiated is present
        init_ev = next((e for e in ev_list if e.event_type == "call.initiated"), None)
        if not init_ev:
            continue  # A call is counted only if call.initiated is present and linked

        lead_id = init_ev.lead_id
        workflow_id = init_ev.workflow_id
        initiated_at = init_ev.occurred_at
        ist_date = to_ist(initiated_at).date()

        is_connected = any(e.event_type == "call.connected" for e in ev_list)
        has_telephony_failed = any(e.event_type == "call.telephony_failed" for e in ev_list)

        # Effective disposition:
        # Latest disposition.override by occurred_at; if none, latest disposition.set by occurred_at
        valid_disp_events = [e for e in ev_list if e.is_valid_disposition and e.disposition_code]
        overrides = [e for e in valid_disp_events if e.event_type == "disposition.override"]
        sets = [e for e in valid_disp_events if e.event_type == "disposition.set"]

        effective_disp: str | None = None
        if overrides:
            # Pick latest override by occurred_at
            overrides.sort(key=lambda e: e.occurred_at)
            effective_disp = overrides[-1].disposition_code
        elif sets:
            # Pick latest set by occurred_at
            sets.sort(key=lambda e: e.occurred_at)
            effective_disp = sets[-1].disposition_code

        is_terminal = False
        is_success = False
        if effective_disp:
            cat_tuple = disp_cat.get((workflow_id, effective_disp))
            if cat_tuple:
                is_terminal, is_success = cat_tuple

        call_facts.append(CallFact(
            call_id=call_id,
            workflow_id=workflow_id,
            lead_id=lead_id,
            initiated_at=initiated_at,
            ist_date=ist_date,
            is_connected=is_connected,
            has_telephony_failed=has_telephony_failed,
            effective_disposition=effective_disp,
            is_terminal=is_terminal,
            is_success=is_success,
        ))

    return call_facts


def compute_lead_bucket(calls: list[CallFact]) -> str:
    """
    Compute lead bucket per priority:
    1. yet_to_reach_out: no calls.
    2. completed: any call's effective disposition is terminal.
    3. in_progress_telephony_failed: every call has a call.telephony_failed event.
    4. in_progress_dialed: everything else.
    """
    if not calls:
        return "yet_to_reach_out"
    if any(c.is_terminal for c in calls):
        return "completed"
    if all(c.has_telephony_failed for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def generate_reports(session: Session, output_dir: Path) -> dict[str, Path]:
    """
    Generate all 5 reports in output_dir.
    Returns mapping of report name to generated file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Make sure all orphans that can be resolved are resolved
    resolve_orphans(session)

    # 1. Compute call facts
    call_facts = compute_call_facts(session)

    # Group call facts by lead_id
    lead_calls_map: dict[int, list[CallFact]] = defaultdict(list)
    for c in call_facts:
        lead_calls_map[c.lead_id].append(c)

    # Sort each lead's calls by initiated_at
    for lid in lead_calls_map:
        lead_calls_map[lid].sort(key=lambda c: c.initiated_at)

    # Fetch all leads
    all_leads = session.query(Lead).all()

    # -------------------------------------------------------------
    # Report 1: batch_summary.csv
    # workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed
    # Sorted by workflow_id, batch_id
    # -------------------------------------------------------------
    batch_counts = defaultdict(lambda: {
        "total_leads": 0,
        "yet_to_reach_out": 0,
        "in_progress_telephony_failed": 0,
        "in_progress_dialed": 0,
        "completed": 0,
    })

    for lead in all_leads:
        bucket = compute_lead_bucket(lead_calls_map.get(lead.id, []))
        key = (lead.workflow_id, lead.batch_id)
        batch_counts[key]["total_leads"] += 1
        batch_counts[key][bucket] += 1

    sorted_batch_keys = sorted(batch_counts.keys(), key=lambda k: (k[0], k[1]))
    batch_summary_path = output_dir / "batch_summary.csv"
    with open(batch_summary_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id", "batch_id", "total_leads",
            "yet_to_reach_out", "in_progress_telephony_failed",
            "in_progress_dialed", "completed"
        ])
        for wf_id, b_id in sorted_batch_keys:
            counts = batch_counts[(wf_id, b_id)]
            writer.writerow([
                wf_id,
                b_id,
                counts["total_leads"],
                counts["yet_to_reach_out"],
                counts["in_progress_telephony_failed"],
                counts["in_progress_dialed"],
                counts["completed"],
            ])

    # -------------------------------------------------------------
    # Report 2: daily_overview.csv
    # workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads
    # Sorted by workflow_id, date
    # -------------------------------------------------------------
    daily_groups = defaultdict(lambda: {
        "total_calls": 0,
        "connected_calls": 0,
        "success_lead_ids": set(),
    })

    for c in call_facts:
        key = (c.workflow_id, c.ist_date)
        daily_groups[key]["total_calls"] += 1
        if c.is_connected:
            daily_groups[key]["connected_calls"] += 1
        if c.is_success:
            daily_groups[key]["success_lead_ids"].add(c.lead_id)

    sorted_daily_keys = sorted(daily_groups.keys(), key=lambda k: (k[0], k[1]))
    daily_overview_path = output_dir / "daily_overview.csv"
    with open(daily_overview_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id", "date", "total_calls",
            "connected_calls", "connectivity_rate", "success_leads"
        ])
        for wf_id, d_val in sorted_daily_keys:
            d_data = daily_groups[(wf_id, d_val)]
            tot = d_data["total_calls"]
            conn = d_data["connected_calls"]
            conn_rate = f"{(conn / tot):.4f}" if tot > 0 else "0.0000"
            writer.writerow([
                wf_id,
                d_val.isoformat(),
                tot,
                conn,
                conn_rate,
                len(d_data["success_lead_ids"]),
            ])

    # -------------------------------------------------------------
    # Report 3: lead_report.csv
    # workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,last_call_at,current_disposition,ever_success
    # Sorted by workflow_id, external_ref
    # -------------------------------------------------------------
    sorted_leads = sorted(all_leads, key=lambda l: (l.workflow_id, l.external_ref))
    lead_report_path = output_dir / "lead_report.csv"
    with open(lead_report_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id", "external_ref", "batch_id", "phone_masked",
            "bucket", "total_calls", "connected_calls", "last_call_at",
            "current_disposition", "ever_success"
        ])
        for lead in sorted_leads:
            calls = lead_calls_map.get(lead.id, [])
            total_calls = len(calls)
            connected_calls = sum(1 for c in calls if c.is_connected)
            bucket = compute_lead_bucket(calls)
            ever_success = "true" if any(c.is_success for c in calls) else "false"

            last_call_at = ""
            current_disposition = ""
            if calls:
                # Latest call.initiated timestamp
                last_call_at = format_iso_ist(calls[-1].initiated_at)

                # Most recent call (by initiated time) that has an effective disposition
                calls_with_disp = [c for c in calls if c.effective_disposition]
                if calls_with_disp:
                    current_disposition = calls_with_disp[-1].effective_disposition

            writer.writerow([
                lead.workflow_id,
                lead.external_ref,
                lead.batch_id,
                lead.phone_masked,
                bucket,
                total_calls,
                connected_calls,
                last_call_at,
                current_disposition,
                ever_success,
            ])

    # -------------------------------------------------------------
    # Report 4: rejections.csv
    # workflow_id,batch_id,row_number,reason
    # Sorted by workflow_id, batch_id, row_number
    # -------------------------------------------------------------
    rejections = (
        session.query(Rejection)
        .order_by(Rejection.workflow_id, Rejection.batch_id, Rejection.row_number)
        .all()
    )
    rejections_path = output_dir / "rejections.csv"
    with open(rejections_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "batch_id", "row_number", "reason"])
        for r in rejections:
            writer.writerow([r.workflow_id, r.batch_id, r.row_number, r.reason])

    # -------------------------------------------------------------
    # Report 5: data_quality.json
    # -------------------------------------------------------------
    data_quality_path = output_dir / "data_quality.json"
    DataQualityManager.write_json(session, data_quality_path)

    return {
        "batch_summary": batch_summary_path,
        "daily_overview": daily_overview_path,
        "lead_report": lead_report_path,
        "rejections": rejections_path,
        "data_quality": data_quality_path,
    }

