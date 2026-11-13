"""
Unit tests for data normalization: phone numbers, dates, currency, enums, timestamps, and headers.
"""
from datetime import datetime, timezone, timedelta
import pytest

from solution.normalizers import (
    format_iso_ist,
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_header,
    normalize_phone,
    parse_timestamp,
    to_ist,
    IST_TZ,
)


def test_normalize_header():
    assert normalize_header("Insured Name") == "insured name"
    assert normalize_header("policy_number") == "policy number"
    assert normalize_header("  mobile___no  ") == "mobile no"
    assert normalize_header("Order_ID") == "order id"
    assert normalize_header("") == ""


def test_normalize_phone_valid():
    # 10 digits starting with 6, 7, 8, 9
    assert normalize_phone("9876543210") == (True, "+919876543210")
    assert normalize_phone("6123456789") == (True, "+916123456789")
    assert normalize_phone("7123456789") == (True, "+917123456789")
    assert normalize_phone("8123456789") == (True, "+918123456789")

    # 11 digits starting with 0
    assert normalize_phone("09876543210") == (True, "+919876543210")
    assert normalize_phone("06832582386") == (True, "+916832582386")

    # 12 digits starting with 91
    assert normalize_phone("919876543210") == (True, "+919876543210")
    assert normalize_phone("91-77187-50036") == (True, "+917718750036")

    # Various punctuation & whitespace formats
    assert normalize_phone("+91 98765 43210") == (True, "+919876543210")
    assert normalize_phone("(+91) 8196106462") == (True, "+918196106462")
    assert normalize_phone("+919312172715") == (True, "+919312172715")


def test_normalize_phone_invalid():
    # Starts with 1, 2, 3, 4, 5
    assert normalize_phone("5123456789") == (False, None)
    assert normalize_phone("1234567890") == (False, None)

    # Wrong length
    assert normalize_phone("987654321") == (False, None)  # 9 digits
    assert normalize_phone("9876543210123") == (False, None)  # 13 digits

    # Non-digit or landline
    assert normalize_phone("022-2000-1000") == (False, None)
    assert normalize_phone("+1 415 555 0134") == (False, None)
    assert normalize_phone("N/A") == (False, None)
    assert normalize_phone("") == (False, None)
    assert normalize_phone(None) == (False, None)


def test_normalize_date_valid():
    # YYYY-MM-DD
    assert normalize_date("2026-11-23") == (True, "2026-11-23")

    # DD/MM/YYYY (day-first)
    assert normalize_date("27/10/2026") == (True, "2026-10-27")
    assert normalize_date("05/11/2026") == (True, "2026-11-05")

    # DD-MM-YYYY (day-first)
    assert normalize_date("21-11-2026") == (True, "2026-11-21")
    assert normalize_date("14-10-2026") == (True, "2026-10-14")

    # DD-Mon-YYYY (case-insensitive)
    assert normalize_date("01-Dec-2026") == (True, "2026-12-01")
    assert normalize_date("29-dec-2026") == (True, "2026-12-29")
    assert normalize_date("21-Nov-2026") == (True, "2026-11-21")


def test_normalize_date_invalid():
    # Impossible dates
    assert normalize_date("31/02/2026") == (False, None)
    assert normalize_date("2026-13-01") == (False, None)
    assert normalize_date("32-10-2026") == (False, None)

    # Strings
    assert normalize_date("soon") == (False, None)
    assert normalize_date("TBD") == (False, None)
    assert normalize_date("") == (False, None)
    assert normalize_date(None) == (False, None)


def test_normalize_decimal_valid():
    assert normalize_decimal("27554") == (True, 27554.0)
    assert normalize_decimal("₹27,554") == (True, 27554.0)
    assert normalize_decimal("Rs. 31629") == (True, 31629.0)
    assert normalize_decimal("Rs 31629") == (True, 31629.0)
    assert normalize_decimal("INR 25301.4") == (True, 25301.4)
    assert normalize_decimal("4985.75") == (True, 4985.75)
    assert normalize_decimal("0") == (True, 0.0)


def test_normalize_decimal_invalid():
    assert normalize_decimal("-100") == (False, None)
    assert normalize_decimal("N/A") == (False, None)
    assert normalize_decimal("-") == (False, None)
    assert normalize_decimal("twelve thousand") == (False, None)
    assert normalize_decimal("") == (False, None)
    assert normalize_decimal(None) == (False, None)


def test_normalize_enum():
    allowed = ["en", "hi", "ta", "kn", "te", "mr"]
    assert normalize_enum("en", allowed) == (True, "en")
    assert normalize_enum("EN", allowed) == (True, "en")
    assert normalize_enum("  kn  ", allowed) == (True, "kn")
    assert normalize_enum("Hindi", allowed) == (False, None)
    assert normalize_enum("english", allowed) == (False, None)
    assert normalize_enum("", allowed) == (False, None)
    assert normalize_enum(None, allowed) == (False, None)


def test_parse_timestamp():
    # UTC
    dt1 = parse_timestamp("2026-10-05T13:20:21Z")
    assert dt1.tzinfo is not None
    assert dt1 == datetime(2026, 10, 5, 13, 20, 21, tzinfo=timezone.utc)

    # Offset (+05:30)
    dt2 = parse_timestamp("2026-10-01T10:40:19+05:30")
    # 10:40:19 IST = 05:10:19 UTC
    assert dt2 == datetime(2026, 10, 1, 5, 10, 19, tzinfo=timezone.utc)

    # Naive timestamp -> means IST per spec
    dt3 = parse_timestamp("2026-10-01 10:40:19")
    assert dt3 == datetime(2026, 10, 1, 5, 10, 19, tzinfo=timezone.utc)


def test_ist_conversion():
    utc_dt = datetime(2026, 10, 14, 12, 25, 24, tzinfo=timezone.utc)
    ist_str = format_iso_ist(utc_dt)
    assert ist_str == "2026-10-14T17:55:24+05:30"

