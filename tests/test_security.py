"""
Unit tests for security, PII protection, deterministic HMAC hashing, and encryption.
"""
import pytest

from solution.security import (
    decrypt_pii,
    deterministic_phone_hash,
    encrypt_pii,
    mask_phone,
)


def test_deterministic_phone_hash():
    phone1 = "+919876543210"
    phone2 = "+919876543210"
    phone3 = "+919876543211"

    hash1 = deterministic_phone_hash(phone1)
    hash2 = deterministic_phone_hash(phone2)
    hash3 = deterministic_phone_hash(phone3)

    assert len(hash1) == 64
    assert hash1 == hash2
    assert hash1 != hash3
    assert deterministic_phone_hash("") == ""


def test_pii_encryption_and_decryption():
    raw_name = "Rahul Sharma"
    encrypted = encrypt_pii(raw_name)

    assert encrypted != raw_name
    assert raw_name not in encrypted

    decrypted = decrypt_pii(encrypted)
    assert decrypted == raw_name


def test_pii_encryption_handles_none():
    assert encrypt_pii(None) is None
    assert decrypt_pii(None) is None


def test_phone_masking():
    assert mask_phone("+919876543210") == "+91******3210"
    assert mask_phone("+917718750036") == "+91******0036"
    assert mask_phone("9876543210") == "+91******3210"
    assert mask_phone("") == ""
    assert mask_phone(None) == ""

