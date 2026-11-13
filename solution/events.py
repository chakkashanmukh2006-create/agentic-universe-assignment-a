"""
Event ingestion and interaction ledger engine.
Handles JSONL event streams, append-only ledger storage, deduplication by event_id,
linking to leads, orphan handling, and resolution of late-arriving leads and overrides.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session

from solution.data_quality import FileQualityMetrics
from solution.models import DispositionMetadata, IngestionLog, InteractionEvent, Lead
from solution.normalizers import normalize_phone, parse_timestamp
from solution.security import deterministic_phone_hash


def _load_disposition_codes(session: Session) -> dict[str, set[str]]:
    """Return a mapping of workflow_id -> set of valid disposition codes."""
    rows = session.query(DispositionMetadata.workflow_id, DispositionMetadata.code).all()
    mapping: dict[str, set[str]] = {}
    for wf, code in rows:
        mapping.setdefault(wf, set()).add(code)
    return mapping


def resolve_orphans(session: Session) -> int:
    """
    Resolve orphaned interaction events whose matching leads or call links
    have now arrived.
    Returns the number of resolved events.
    """
    resolved_count = 0

    # 1. Resolve phone-based orphans: match on (workflow_id, phone_hash)
    orphans_with_phone = (
        session.query(InteractionEvent)
        .filter(
            InteractionEvent.is_orphan.is_(True),
            InteractionEvent.phone_hash.isnot(None),
        )
        .all()
    )

    if orphans_with_phone:
        # Group by (workflow_id, phone_hash)
        wf_hashes = {(e.workflow_id, e.phone_hash) for e in orphans_with_phone}
        phone_hashes = list({ph for _, ph in wf_hashes})
        workflows = list({wf for wf, _ in wf_hashes})
        
        # Query leads in chunks to avoid SQLite parameter limit on large datasets
        lead_map = {}
        chunk_size = 500
        for i in range(0, len(phone_hashes), chunk_size):
            p_chunk = phone_hashes[i:i + chunk_size]
            leads = (
                session.query(Lead.id, Lead.workflow_id, Lead.phone_hash)
                .filter(
                    Lead.workflow_id.in_(workflows),
                    Lead.phone_hash.in_(p_chunk),
                )
                .all()
            )
            for l in leads:
                lead_map[(l.workflow_id, l.phone_hash)] = l.id

        for ev in orphans_with_phone:
            key = (ev.workflow_id, ev.phone_hash)
            if key in lead_map:
                ev.lead_id = lead_map[key]
                ev.is_orphan = False
                resolved_count += 1

    # 2. Resolve call_id-based orphans (e.g. disposition.override or events with no direct phone)
    # If any event in the call is linked to a lead, propagate that lead_id & phone_hash
    # to all other events of that call
    orphans_without_lead = (
        session.query(InteractionEvent)
        .filter(InteractionEvent.is_orphan.is_(True))
        .all()
    )

    if orphans_without_lead:
        call_ids = list({e.call_id for e in orphans_without_lead})
        call_lead_map = {}
        chunk_size = 500
        for i in range(0, len(call_ids), chunk_size):
            c_chunk = call_ids[i:i + chunk_size]
            linked_events = (
                session.query(InteractionEvent.call_id, InteractionEvent.lead_id, InteractionEvent.phone_hash)
                .filter(
                    InteractionEvent.call_id.in_(c_chunk),
                    InteractionEvent.lead_id.isnot(None),
                )
                .all()
            )
            for row in linked_events:
                call_lead_map[row.call_id] = (row.lead_id, row.phone_hash)

        for ev in orphans_without_lead:
            if ev.call_id in call_lead_map:
                lid, ph = call_lead_map[ev.call_id]
                ev.lead_id = lid
                if not ev.phone_hash and ph:
                    ev.phone_hash = ph
                ev.is_orphan = False
                resolved_count += 1

    if resolved_count > 0:
        session.flush()

    return resolved_count


def ingest_event_file(
    file_path: Path,
    session: Session,
) -> FileQualityMetrics:
    """
    Ingest a single events JSONL file idempotently into the append-only ledger.
    """
    metrics = FileQualityMetrics()
    rel_path_str = str(file_path)

    # Idempotency check: if file already processed, return existing logged metrics
    existing_log = session.query(IngestionLog).filter_by(file_path=rel_path_str).first()
    if existing_log:
        metrics.events_total = existing_log.events_total
        metrics.malformed_events = existing_log.malformed_events
        metrics.duplicate_events_dropped = existing_log.duplicate_events_dropped
        metrics.orphan_events = existing_log.orphan_events
        metrics.unknown_disposition_events = existing_log.unknown_disposition_events
        return metrics

    valid_dispositions = _load_disposition_codes(session)

    # Cache existing event_ids in memory for fast deduplication check
    existing_event_ids: set[str] = {
        row[0] for row in session.query(InteractionEvent.event_id).all()
    }

    # Cache existing calls that already have lead_id
    call_lead_cache: dict[str, tuple[int, str]] = {
        row.call_id: (row.lead_id, row.phone_hash)
        for row in session.query(InteractionEvent.call_id, InteractionEvent.lead_id, InteractionEvent.phone_hash)
        .filter(InteractionEvent.lead_id.isnot(None))
        .all()
    }

    # Cache existing leads by (workflow_id, phone_hash)
    lead_cache: dict[tuple[str, str], int] = {
        (l.workflow_id, l.phone_hash): l.id
        for l in session.query(Lead.workflow_id, Lead.phone_hash, Lead.id).all()
    }

    events_to_insert = []

    with open(file_path, mode="r", encoding="utf-8", errors="replace") as f:
        for line_raw in f:
            line = line_raw.strip()
            if not line:
                continue  # skip pure blank lines

            metrics.events_total += 1

            # 1. Malformed check
            try:
                ev_data = json.loads(line)
                if not isinstance(ev_data, dict) or "event_id" not in ev_data or not ev_data["event_id"]:
                    metrics.malformed_events += 1
                    continue
            except Exception:
                metrics.malformed_events += 1
                continue

            event_id = str(ev_data["event_id"]).strip()
            if not event_id:
                metrics.malformed_events += 1
                continue

            # 2. Duplicate check
            if event_id in existing_event_ids:
                metrics.duplicate_events_dropped += 1
                continue
            existing_event_ids.add(event_id)

            event_type = ev_data.get("event_type", "")
            workflow_id = ev_data.get("workflow_id", "")
            call_id = ev_data.get("call_id", "")
            occurred_at_raw = ev_data.get("occurred_at", "")
            raw_phone = ev_data.get("phone")
            payload = ev_data.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}

            # Parse occurred_at
            try:
                occurred_at = parse_timestamp(occurred_at_raw)
            except Exception:
                # If timestamp is malformed, count as malformed
                metrics.malformed_events += 1
                continue

            # 3. Phone normalization and deterministic hashing
            p_hash = None
            if raw_phone:
                is_valid_phone, canon_phone = normalize_phone(raw_phone)
                if is_valid_phone and canon_phone:
                    p_hash = deterministic_phone_hash(canon_phone)

            # 4. Linking to lead
            lead_id = None
            is_orphan = True

            if p_hash and (workflow_id, p_hash) in lead_cache:
                lead_id = lead_cache[(workflow_id, p_hash)]
                is_orphan = False
                call_lead_cache[call_id] = (lead_id, p_hash)
            elif call_id in call_lead_cache:
                lead_id, cached_ph = call_lead_cache[call_id]
                if not p_hash and cached_ph:
                    p_hash = cached_ph
                is_orphan = False

            # 5. Unknown disposition check
            is_valid_disp = True
            disp_code = None
            if event_type in ("disposition.set", "disposition.override"):
                disp_code = payload.get("disposition_code")
                if disp_code is not None:
                    disp_code = str(disp_code).strip()
                    allowed_codes = valid_dispositions.get(workflow_id, set())
                    if disp_code not in allowed_codes:
                        metrics.unknown_disposition_events += 1
                        is_valid_disp = False

            event_obj = InteractionEvent(
                event_id=event_id,
                event_type=event_type,
                workflow_id=workflow_id,
                call_id=call_id,
                occurred_at=occurred_at,
                phone_hash=p_hash,
                lead_id=lead_id,
                is_orphan=is_orphan,
                disposition_code=disp_code,
                is_valid_disposition=is_valid_disp,
                payload_json=json.dumps(payload),
            )
            events_to_insert.append(event_obj)

    if events_to_insert:
        session.bulk_save_objects(events_to_insert)
        session.flush()

    # Reconcile any remaining orphans in the call group
    resolve_orphans(session)

    # Ingestion log
    log = IngestionLog(
        file_path=rel_path_str,
        file_type="event",
        events_total=metrics.events_total,
        malformed_events=metrics.malformed_events,
        duplicate_events_dropped=metrics.duplicate_events_dropped,
        orphan_events=metrics.orphan_events,
        unknown_disposition_events=metrics.unknown_disposition_events,
    )
    session.add(log)
    session.flush()

    return metrics

