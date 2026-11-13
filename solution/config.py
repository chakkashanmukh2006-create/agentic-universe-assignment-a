"""
Configuration and settings for the Interaction Ledger & Reporting Pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = BASE_DIR / "assignment-a-data" / "schemas"

# Database Configuration
# Default to SQLite database in the workspace if DATABASE_URL is not set
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{BASE_DIR / 'interaction_ledger.db'}"
)

# Security Configuration
# Deterministic HMAC salt/key for phone hashing (deterministic lookup)
# Authenticated encryption key for PII at rest (Fernet key)
PII_HMAC_SECRET = os.environ.get(
    "PII_HMAC_SECRET",
    "au-default-deterministic-hmac-secret-salt-2026"
)

# 32-byte base64-encoded key for Fernet encryption
# Default development key (32 url-safe base64-encoded bytes)
PII_ENCRYPTION_KEY = os.environ.get(
    "PII_ENCRYPTION_KEY",
    "c2VjdXJlLWVuY3J5cHRpb24ta2V5LTIwMjYtMDAwMA=="  # 32 bytes base64
)

# IST Timezone representation
IST_OFFSET_HOURS = 5
IST_OFFSET_MINUTES = 30

