"""
Lead ingestion engine.
Handles CSV parsing, header mapping against workflow schemas, normalization,
schema-order validation, rejections, duplicate merging, and PII protection.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session

from solution.config import SCHEMAS_DIR
from solution.data_quality import FileQualityMetrics
from solution.models import IngestionLog, Lead, Rejection
from solution.normalizers import (
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_header,
    normalize_phone,
)
from solution.security import (
    deterministic_phone_hash,
    encrypt_pii,
    mask_phone,
)


@dataclass
class SchemaField:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    aliases: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    default: Any = None


@dataclass
class WorkflowSchema:
    workflow_id: str
    description: str
    fields: list[SchemaField]

    @classmethod
    def load(cls, workflow_id: str, schemas_dir: Path | None = None) -> WorkflowSchema:
        dir_path = schemas_dir or SCHEMAS_DIR
        schema_file = dir_path / f"{workflow_id}.json"
        if not schema_file.exists():
            raise FileNotFoundError(f"Schema not found for workflow: {workflow_id} at {schema_file}")

        with open(schema_file, mode="r", encoding="utf-8") as f:
            data = json.load(f)

        fields = []
        for f_data in data.get("fields", []):
            fields.append(SchemaField(
                name=f_data["name"],
                type=f_data.get("type", "string"),
                required=f_data.get("required", False),
                pii=f_data.get("pii", False),
                reference=f_data.get("reference", False),
                aliases=f_data.get("aliases", []),
                values=f_data.get("values", []),
                default=f_data.get("default"),
            ))

        return cls(
            workflow_id=data["workflow_id"],
            description=data.get("description", ""),
            fields=fields,
        )


def _build_header_map(csv_headers: list[str], schema: WorkflowSchema) -> dict[str, SchemaField]:
    """
    Map CSV header to corresponding SchemaField.
    Normalized by lowercase, trimmed, whitespace/_ replaced with single space.
    Columns matching nothing are omitted.
    """
    header_map = {}
    normalized_fields = {}
    for sf in schema.fields:
        norm_name = normalize_header(sf.name)
        normalized_fields[norm_name] = sf
        for alias in sf.aliases:
            norm_alias = normalize_header(alias)
            normalized_fields[norm_alias] = sf

    for raw_header in csv_headers:
        norm_header = normalize_header(raw_header)
        if norm_header in normalized_fields:
            header_map[raw_header] = normalized_fields[norm_header]

    return header_map


def _is_blank_row(row: dict[str, str | None]) -> bool:
    """Check if all cells in a row are empty or pure whitespace."""
    for val in row.values():
        if val is not None and str(val).strip():
            return False
    return True


def _is_blank_line(line: str) -> bool:
    """Check if a raw line consists only of commas, whitespace, or newlines."""
    return not line.strip() or not line.replace(",", "").strip()


def ingest_lead_file(
    file_path: Path,
    workflow_id: str,
    session: Session,
    schemas_dir: Path | None = None,
) -> FileQualityMetrics:
    """
    Ingest a single lead CSV file idempotently into the database.
    """
    metrics = FileQualityMetrics()
    rel_path_str = str(file_path)
    batch_id = file_path.stem

    # Idempotency check: if file already processed, return existing logged metrics
    existing_log = session.query(IngestionLog).filter_by(file_path=rel_path_str).first()
    if existing_log:
        metrics.lead_rows_total = existing_log.lead_rows_total
        metrics.lead_rows_rejected = existing_log.lead_rows_rejected
        metrics.duplicate_lead_rows_merged = existing_log.duplicate_lead_rows_merged
        metrics.field_warnings = existing_log.field_warnings
        return metrics

    schema = WorkflowSchema.load(workflow_id, schemas_dir)

    with open(file_path, mode="r", encoding="utf-8", errors="replace") as f:
        # Filter out leading blank lines before header if any
        lines = []
        header_found = False
        for line in f:
            if not header_found:
                if _is_blank_line(line):
                    continue
                header_found = True
            lines.append(line)

        if not lines:
            return metrics

        reader = csv.DictReader(lines)
        if not reader.fieldnames:
            return metrics

        header_map = _build_header_map(reader.fieldnames, schema)

        row_number = 0
        for raw_row in reader:
            if _is_blank_row(raw_row):
                continue  # Blank rows skipped and not counted

            row_number += 1
            metrics.lead_rows_total += 1

            # Extract row values keyed by schema field name
            row_data: dict[str, str | None] = {}
            for col_name, raw_val in raw_row.items():
                if col_name in header_map:
                    sf = header_map[col_name]
                    v = str(raw_val).strip() if raw_val is not None else ""
                    row_data[sf.name] = v if v else None

            # Walk fields in SCHEMA ORDER for validation
            is_rejected = False
            rejection_reason = None
            parsed_values: dict[str, Any] = {}
            row_warnings = 0

            for sf in schema.fields:
                val = row_data.get(sf.name)

                # Check required missing
                if sf.required and val is None:
                    is_rejected = True
                    rejection_reason = f"missing_required:{sf.name}"
                    break

                # Validate and normalize based on field type
                if sf.type == "phone":
                    if val is not None:
                        valid_phone, canon_phone = normalize_phone(val)
                        if not valid_phone:
                            is_rejected = True
                            rejection_reason = "invalid_phone"
                            break
                        parsed_values[sf.name] = canon_phone
                    else:
                        parsed_values[sf.name] = None

                elif sf.type == "string":
                    parsed_values[sf.name] = val

                elif sf.type == "date":
                    if val is not None:
                        valid_d, date_str = normalize_date(val)
                        if valid_d:
                            parsed_values[sf.name] = date.fromisoformat(date_str)
                        else:
                            parsed_values[sf.name] = None
                            row_warnings += 1
                    else:
                        parsed_values[sf.name] = None

                elif sf.type == "decimal":
                    if val is not None:
                        valid_num, dec_val = normalize_decimal(val)
                        if valid_num:
                            parsed_values[sf.name] = dec_val
                        else:
                            parsed_values[sf.name] = None
                            row_warnings += 1
                    else:
                        parsed_values[sf.name] = None

                elif sf.type == "enum":
                    if val is not None:
                        valid_enum, enum_val = normalize_enum(val, sf.values)
                        if valid_enum:
                            parsed_values[sf.name] = enum_val
                        else:
                            parsed_values[sf.name] = None
                            row_warnings += 1
                    else:
                        parsed_values[sf.name] = None

            if is_rejected:
                metrics.add_rejection(rejection_reason)
                rejection = Rejection(
                    workflow_id=workflow_id,
                    batch_id=batch_id,
                    row_number=row_number,
                    reason=rejection_reason,
                )
                session.add(rejection)
                continue  # Rejected rows are not leads

            # Non-rejected row: accumulate warnings
            metrics.field_warnings += row_warnings

            # Lead Identity: (workflow_id, canonical_phone)
            canon_phone = parsed_values.get("phone")
            p_hash = deterministic_phone_hash(canon_phone)

            # Check if lead already exists
            existing_lead = (
                session.query(Lead)
                .filter_by(workflow_id=workflow_id, phone_hash=p_hash)
                .first()
            )

            # Identify reference field (e.g. policy_number or order_id)
            ref_field_name = next((sf.name for sf in schema.fields if sf.reference), None)
            external_ref_val = parsed_values.get(ref_field_name) if ref_field_name else ""

            # Check dynamic default for preferred_language or other fields
            lang_val = parsed_values.get("preferred_language")
            if lang_val is None:
                lang_field = next((sf for sf in schema.fields if sf.name == "preferred_language"), None)
                lang_val = lang_field.default if (lang_field and lang_field.default) else "en"

            if existing_lead is None:
                # First valid row creates lead, batch_id permanently set to current batch
                lead = Lead(
                    workflow_id=workflow_id,
                    phone_hash=p_hash,
                    phone_masked=mask_phone(canon_phone),
                    phone_encrypted=encrypt_pii(canon_phone),
                    customer_name_encrypted=encrypt_pii(parsed_values.get("customer_name")),
                    external_ref=external_ref_val,
                    batch_id=batch_id,
                    product=parsed_values.get("product"),
                    policy_expiry=parsed_values.get("policy_expiry"),
                    order_date=parsed_values.get("order_date"),
                    premium_amount=parsed_values.get("premium_amount"),
                    preferred_language=lang_val,
                )
                session.add(lead)
                session.flush()  # assign lead.id
            else:
                # Duplicate row: merge and update non-null fields only (last write wins)
                metrics.duplicate_lead_rows_merged += 1

                if parsed_values.get("customer_name") is not None:
                    existing_lead.customer_name_encrypted = encrypt_pii(parsed_values["customer_name"])
                if external_ref_val is not None:
                    existing_lead.external_ref = external_ref_val
                if parsed_values.get("product") is not None:
                    existing_lead.product = parsed_values["product"]
                if parsed_values.get("policy_expiry") is not None:
                    existing_lead.policy_expiry = parsed_values["policy_expiry"]
                if parsed_values.get("order_date") is not None:
                    existing_lead.order_date = parsed_values["order_date"]
                if parsed_values.get("premium_amount") is not None:
                    existing_lead.premium_amount = parsed_values["premium_amount"]
                if parsed_values.get("preferred_language") is not None:
                    existing_lead.preferred_language = parsed_values["preferred_language"]

    # Record IngestionLog
    log = IngestionLog(
        file_path=rel_path_str,
        file_type="lead",
        workflow_id=workflow_id,
        batch_id=batch_id,
        lead_rows_total=metrics.lead_rows_total,
        lead_rows_rejected=metrics.lead_rows_rejected,
        duplicate_lead_rows_merged=metrics.duplicate_lead_rows_merged,
        field_warnings=metrics.field_warnings,
    )
    session.add(log)
    session.flush()

    # Resolve any events that arrived before their leads
    from solution.events import resolve_orphans
    resolve_orphans(session)

    return metrics
