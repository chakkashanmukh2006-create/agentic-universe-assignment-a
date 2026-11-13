# Agentic Universe: Assignment A Solution
## Interaction Ledger & Reporting Pipeline

This directory contains the production-quality implementation of **Assignment A: Interaction Ledger & Reporting Pipeline**.

### System Architecture

- **`config.py`**: Pipeline settings, schema paths, database connection strings, and security secret keys.
- **`security.py`**: PII protection subsystem implementing deterministic HMAC-SHA256 phone hashing for indexed joins, Fernet authenticated encryption for PII at rest, and phone masking (`+91******XXXX`).
- **`normalizers.py`**: Strict normalizers for Indian phone numbers (`+91XXXXXXXXXX`), day-first dates (`YYYY-MM-DD`), decimals with currency prefixes (`₹`, `Rs.`, `INR`), enums, and timestamps (UTC, offset, naive as IST).
- **`models.py`**: SQLAlchemy relational models for `Lead`, `InteractionEvent` (append-only ledger), `Rejection` (audit trail), `IngestionLog` (idempotency tracker), and `DispositionMetadata`.
- **`database.py`**: Database engine and session management with SQLite and PostgreSQL support.
- **`leads.py`**: Lead ingestion engine with dynamic schema header mapping, blank row skipping, schema-order rejection, duplicate merging, and default value application.
- **`events.py`**: Event ingestion engine with malformed line filtering, event deduplication, call linking, and dynamic orphan resolution.
- **`data_quality.py`**: Dedicated data quality tracker monitoring metrics and generating `data_quality.json`.
- **`reporting.py`**: Business calculation and report generation engine producing all 5 contract reports.
- **`main.py`**: Typer CLI application.

### CLI Usage

```bash
# 1. Ingest Data Drops (idempotent and append-only)
python -m solution.main ingest data/drop_1
python -m solution.main ingest data/drop_2

# 2. Generate Reports
python -m solution.main report --out reports/

# 3. Optional Database Reset
python -m solution.main reset
```

### Running Tests

```bash
pytest
```
