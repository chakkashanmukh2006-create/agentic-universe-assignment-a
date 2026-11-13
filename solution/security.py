"""
Security module for PII protection.
Provides deterministic HMAC hashing for phone matching,
authenticated Fernet encryption for PII at rest,
and phone masking for reporting.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from cryptography.fernet import Fernet

from solution.config import PII_ENCRYPTION_KEY, PII_HMAC_SECRET


def _get_fernet_key(raw_secret: str) -> bytes:
    """Derive a valid 32-byte URL-safe base64-encoded key from an arbitrary string."""
    try:
        # Check if already a valid 32-byte base64 Fernet key
        decoded = base64.urlsafe_b64decode(raw_secret.encode())
        if len(decoded) == 32:
            return raw_secret.encode()
    except Exception:
        pass
    # Derive deterministic 32-byte key using SHA256
    digest = hashlib.sha256(raw_secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


_FERNET = Fernet(_get_fernet_key(PII_ENCRYPTION_KEY))


def deterministic_phone_hash(canonical_phone: str) -> str:
    """
    Compute a deterministic HMAC-SHA256 hash for a canonical phone number (+91XXXXXXXXXX).
    Allows exact joins and uniqueness checks without storing raw phone in plaintext.
    """
    if not canonical_phone:
        return ""
    return hmac.new(
        PII_HMAC_SECRET.encode("utf-8"),
        canonical_phone.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def encrypt_pii(plaintext: str | None) -> str | None:
    """Encrypt PII using authenticated Fernet encryption."""
    if plaintext is None:
        return None
    return _FERNET.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_pii(ciphertext: str | None) -> str | None:
    """Decrypt Fernet ciphertext back to plaintext."""
    if ciphertext is None:
        return None
    return _FERNET.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def mask_phone(canonical_phone: str | None) -> str:
    """
    Format phone as +91****** followed by the last 4 digits.
    Example: +919876543210 -> +91******3210
    """
    if not canonical_phone:
        return ""
    digits = "".join(c for c in canonical_phone if c.isdigit())
    if len(digits) >= 4:
        last4 = digits[-4:]
        return f"+91******{last4}"
    return "+91******0000"

