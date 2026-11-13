# Submission

## Candidate
- **Name:** Shanmukh
- **Assignment chosen:** A (Data: Interaction Ledger & Reporting Pipeline)
- **Time spent (honest estimate):** 4.5h

## How to run
```bash
# 1. Environment setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Generate test data (if not already generated)
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# 3. Ingest drops idempotently
python -m solution.main ingest data/drop_1
python -m solution.main ingest data/drop_2

# 4. Generate all five report files
python -m solution.main report --out reports/

# 5. Run automated test suite
pytest
```

## Design

The pipeline is architected around an **append-only interaction ledger** that acts as the immutable single source of truth for all campaign communications, coupled with a deduplicated relational `leads` store and a PII security boundary.

```
+------------------------------------------------------------------------------------------------+
|                                    INGESTION PIPELINE                                          |
|                                                                                                |
|   +--------------------+       Header Normalization & Schema Order Validation                  |
|   |   Lead CSV Files   | ----> Deterministic HMAC Hash + Fernet Encryption                     |
|   +--------------------+       Upsert into `leads` table (first batch_id permanent)            |
|                                         |                                                      |
|                                         v (triggers)                                           |
|   +--------------------+       JSONL Parse & Dedup by event_id                                 |
|   | Event JSONL Files  | ----> Append to `interaction_events` ledger                           |
|   +--------------------+       Link / Resolve Orphans via (workflow_id, phone_hash) or call_id |
+-----------------------------------------+------------------------------------------------------+
                                          |
                                          v
+------------------------------------------------------------------------------------------------+
|                                     DATABASE SCHEMA                                            |
|                                                                                                |
|   leads:                                                                                       |
|     id, workflow_id, phone_hash (indexed), phone_masked, phone_encrypted,                      |
|     customer_name_encrypted, external_ref, batch_id, preferred_language, ...                   |
|                                                                                                |
|   interaction_events (Append-Only):                                                            |
|     event_id (PK), event_type, workflow_id, call_id, occurred_at (UTC),                        |
|     phone_hash, lead_id (FK), is_orphan, disposition_code, is_valid_disposition, payload       |
|                                                                                                |
|   rejections:                                                                                  |
|     id (PK), workflow_id, batch_id, row_number, reason (NO PII)                                |
|                                                                                                |
|   ingestion_history:                                                                           |
|     file_path (PK), file_type, metrics & counts for strict idempotency                         |
+-----------------------------------------+------------------------------------------------------+
                                          |
                                          v
+------------------------------------------------------------------------------------------------+
|                                    REPORTING ENGINE                                            |
|                                                                                                |
|   Computes derived facts (Calls, Connected Calls, Effective Dispositions, Lead Buckets)       |
|   Exports:                                                                                     |
|     - batch_summary.csv        - daily_overview.csv        - lead_report.csv                   |
|     - rejections.csv           - data_quality.json                                             |
+------------------------------------------------------------------------------------------------+
```

### Relational Schema & Ledger Rationale
1. **Append-Only Interaction Ledger (`interaction_events`)**:
   - `event_id` serves as the primary key, immediately preventing duplicate ingestion.
   - Supervisor overrides (`disposition.override`) do **not** update or delete previous records; they are inserted as new events in the ledger.
   - Effective disposition is derived at query/reporting time by taking the latest valid override by `occurred_at`, or falling back to the latest valid `disposition.set`.
2. **PII Protection at Rest**:
   - Plaintext phone numbers and customer names are never stored in the database.
   - We compute `phone_hash = HMAC-SHA256(canonical_phone, secret_key)`, which allows $O(1)$ indexed joins and deduplication without exposing raw digits.
   - `phone_encrypted` and `customer_name_encrypted` use authenticated symmetric encryption (Fernet / AES-128-CBC + HMAC) for recoverable needs.
   - `phone_masked` (`+91******XXXX`) is stored directly to allow report generation with zero cryptographic decryption overhead.
3. **Dynamic Orphan Resolution**:
   - Events that arrive before their lead (or overrides that arrive before call events) are quarantined as `is_orphan = True`.
   - Ingesting a subsequent batch automatically triggers `resolve_orphans()`, which reconciles phone hashes and call groups, linking events and updating final quality metrics.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| **Deterministic HMAC for Phone Matching** | Allows exact indexing and relational joins between leads and events without decrypting PII or storing plaintext numbers. | Storing raw plaintext phone numbers (violates security spec) or decrypting all phones in application memory on every join (poor performance). |
| **Store Timestamps in UTC, convert to IST on Read** | Standardizes datetime comparisons across diverse inputs (Z, +05:30 offset, naive) while ensuring exact IST date grouping for client daily reports. | Storing naive strings or mixed offsets in the database. |
| **Strict Schema-Order Field Validation** | Guarantees deterministic, reproducible rejection reasons matching the first failure in schema order as required by spec. | Multi-error reporting per row or arbitrary dict iteration order. |
| **File-Level & Record-Level Idempotency** | Processing the same drop twice detects logged file signatures, skipping duplicate work while preserving exact data quality metrics. | Blind re-insertions relying solely on database constraint violations. |
| **Store `phone_masked` on Lead Record** | Generates `lead_report.csv` in milliseconds without requiring on-the-fly decryption of thousands of encrypted phone fields. | On-demand decryption of encrypted PII during report generation. |
| **Dynamic Orphan Resolution Hook** | Ensures that order of drop delivery does not impact the final report state (Drop 1 then Drop 2 yields identical state to ingesting all at once). | Static orphan quarantine requiring manual re-indexing. |

## Failure modes considered

- **Malformed JSON & Corrupted Lines:** Event parser catches JSONDecodeErrors and missing `event_id` fields, incrementing `malformed_events` without halting the pipeline.
- **Unquoted Comma Splitting:** Lead CSV parser handles currency values containing commas (`"₹27,554"`) by strictly adhering to RFC 4180 delimiter parsing.
- **Missing Required vs Invalid Optional Fields:** Missing required fields immediately reject the row; invalid optional fields set the column to null and increment `field_warnings` only on valid rows.
- **Re-runs and Crashes:** Transactions are used per drop with checkpoints in `ingestion_history`. Duplicate events are skipped safely via `event_id` set lookup.
- **Network / Database Outages:** SQLite connection timeouts are configured with busy handlers; PostgreSQL support is included out-of-the-box via SQLAlchemy.
- **What is NOT handled:** Multi-node distributed file locking across multiple concurrent writers (assumed single-process or sequential drop pipeline for this deployment).

## Testing

Comprehensive automated testing is implemented using `pytest` (29 tests across 6 test modules):

1. **`test_normalizers.py`**:
   - Header cleaning (underscores, whitespace, casing).
   - Phone normalization (10-digit Indian mobiles, stripping leading 0 and 91, rejecting landlines, international, short/long numbers).
   - Day-first date parsing (`YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `DD-Mon-YYYY`) and rejection of impossible calendar dates (`31/02/2026`).
   - Decimal cleaning (`₹`, `Rs`, `Rs.`, `INR`, comma separators, rounding to 2 places).
   - Enum matching (case-insensitivity, whitespace trimming).
   - Timestamp parsing (UTC, offset, naive as IST).
2. **`test_security.py`**:
   - Deterministic HMAC-SHA256 uniqueness and consistency.
   - Authenticated Fernet encryption and decryption round-tripping.
   - Masked phone formatting (`+91******XXXX`).
3. **`test_leads.py`**:
   - Dynamic header aliases and extra column ignoring (`agent_notes`).
   - Skipping blank rows without incrementing row counters.
   - Schema-order rejection priorities.
   - Duplicate lead merging and permanent `batch_id` preservation.
4. **`test_events.py`**:
   - Malformed line skipping and event deduplication.
   - Direct lead linking and late-arriving lead orphan resolution.
   - Supervisor overrides linking via `call_id` without phone.
   - Append-only ledger verification (overrides do not modify previous events).
   - Unknown disposition code detection.
5. **`test_reporting.py`**:
   - Lead bucket priority (`yet_to_reach_out` -> `completed` -> `in_progress_telephony_failed` -> `in_progress_dialed`).
   - Report format and 4-decimal connectivity rate precision.
6. **`test_idempotency.py`**:
   - End-to-end multi-drop execution and verification that re-running drop 1 changes zero records and zero counts.

All 29 tests pass consistently in under 2.5 seconds.

## Results

- **Scale Small Run (Seed 42):**
  - **Drop 1 Ingestion:** 1,515 lead rows, 4,247 events (1.1s)
  - **Drop 2 Ingestion:** 542 lead rows, 4,498 events (0.9s)
  - **Report Generation:** All 5 reports produced in 0.15s
  - **Total Pipeline Execution Time:** ~2.15 seconds
  - **Data Quality Invariants:**
    - Total lead rows: 2,057 = 1,892 unique leads + 108 rejected + 57 merged duplicates (100% exact reconciliation).
    - Batch summary bucket totals sum exactly to `total_leads` for each batch.
    - Zero plaintext PII present in database tables or reports.

## If I had another day
1. **SQL-Native Window Function Aggregations:** Implement the reporting aggregations entirely within SQL CTEs and window functions (`ROW_NUMBER() OVER (PARTITION BY call_id ORDER BY occurred_at DESC)`) for maximum database pushdown.
2. **Chunked Streaming for 100k+ Leads/Day:** Implement chunked generator streaming with batch inserts (`yield_per` / `bulk_insert_mappings`) to scale linearly to 100k+ leads/day with constant RAM usage (< 150MB).
3. **Key Management Service (KMS) Integration:** Integrate with AWS KMS or HashiCorp Vault for envelope encryption of PII secrets rather than static environment keys.
