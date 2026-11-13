# AI Usage Disclosure

## Tools used
- Gemini / Antigravity Agent (Google DeepMind)

## What I used them for
- Rapid exploration and verification of the dataset generator and workflow JSON schemas.
- Drafting initial boilerplate for Typer CLI commands and standard library date parsing routines.
- Formulating comprehensive test fixtures covering edge cases (e.g. unquoted comma in CSV decimals, impossible dates like February 31st).

## What I wrote or decided myself
- **Core Architecture & Data Modeling:** The database schema design, specifically the separation of deduplicated leads and the immutable append-only interaction ledger (`interaction_events`).
- **PII Protection Strategy:** Decoupling lookup identity from recoverable PII by using deterministic HMAC-SHA256 hashing for relational matching and Fernet symmetric encryption for encrypted storage.
- **Dynamic Orphan Resolution Logic:** Designing the bidirectional orphan linker that resolves previously unlinked events when late leads arrive in subsequent data drops.
- **Strict Invariant Enforcement:** Defining exact mathematical invariants across `batch_summary`, `daily_overview`, `rejections`, and `data_quality.json`.

## Where the AI was wrong or unhelpful
- **CSV Dialect and Delimiter Handling:** An initial test script drafted with AI created CSV test content where `"₹27,554"` was written without quotes (`₹27,554`), causing Python's standard `csv.DictReader` to split the amount into two columns (`₹27` and `554`), leading to an unexpected field warning on language. I identified this by inspecting the parsed dictionary and corrected the CSV formatting to wrap currency values with quotes.
- **Orphan Override Linking:** The AI initially assumed overrides always had phone numbers. I reviewed the specification in `assignment-a-data/README.md` and corrected the linking logic so overrides link through `call_id` to the lead of the other events of that call.

## How I verified AI-generated code
- **Automated Test Suite:** Created 29 automated unit and integration tests executed using `pytest`.
- **Differential Multi-Seed Runs:** Generated datasets with `--seed 42` and `--seed 101` and validated that the pipeline produced clean, non-hardcoded outputs across both runs.
- **Invariance Checks:** Verified that `total_leads = yet_to_reach_out + in_progress_telephony_failed + in_progress_dialed + completed` and `lead_rows_total = unique_leads + rejected + merged_duplicates`.
