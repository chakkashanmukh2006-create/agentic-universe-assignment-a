"""
Normalization module for headers, phone numbers, dates, decimals, enums, and timestamps.
Adheres strictly to the specification in assignment-a-data/README.md.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

# Standard IST timezone: UTC+05:30
IST_TZ = timezone(timedelta(hours=5, minutes=30))

# Month name mapping for case-insensitive DD-Mon-YYYY
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def normalize_header(header: str) -> str:
    """
    Normalise a header: lower-case it, trim it, and replace every run of '_' or
    whitespace with a single space.
    """
    if not header:
        return ""
    return re.sub(r"[\s_]+", " ", header.strip().lower())


def normalize_phone(val: str | None) -> tuple[bool, str | None]:
    """
    Normalize Indian mobile number.
    Strip every non-digit.
    If 12 digits starting with '91', drop the '91'.
    If 11 digits starting with '0', drop the '0'.
    Valid only if exactly 10 digits and starts with 6, 7, 8, or 9.
    Canonical format: +91XXXXXXXXXX.
    Returns: (is_valid, canonical_phone)
    """
    if val is None:
        return False, None
    s = str(val).strip()
    if not s:
        return False, None

    # Strip all non-digit characters
    digits = re.sub(r"\D", "", s)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return True, f"+91{digits}"
    return False, None


def normalize_date(val: str | None) -> tuple[bool, str | None]:
    """
    Normalize date string.
    Accepted formats:
    - YYYY-MM-DD
    - DD/MM/YYYY
    - DD-MM-YYYY
    - DD-Mon-YYYY (e.g. 01-Dec-2026; month is case-insensitive)
    Dates are day-first for ambiguous cases.
    Impossible dates (e.g. 31/02) are invalid.
    Returns: (is_valid, ISO date string YYYY-MM-DD)
    """
    if val is None:
        return False, None
    s = str(val).strip()
    if not s:
        return False, None

    # 1. Try DD-Mon-YYYY (e.g. 01-Dec-2026 or 1-dec-2026)
    m = re.match(r"^(\d{1,2})-([a-zA-Z]{3})-(\d{4})$", s)
    if m:
        day_str, mon_str, year_str = m.groups()
        mon_lower = mon_str.lower()
        if mon_lower in MONTHS:
            try:
                d = date(int(year_str), MONTHS[mon_lower], int(day_str))
                return True, d.isoformat()
            except ValueError:
                return False, None

    # 2. Try YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        year_str, month_str, day_str = m.groups()
        try:
            d = date(int(year_str), int(month_str), int(day_str))
            return True, d.isoformat()
        except ValueError:
            return False, None

    # 3. Try DD/MM/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        day_str, month_str, year_str = m.groups()
        try:
            d = date(int(year_str), int(month_str), int(day_str))
            return True, d.isoformat()
        except ValueError:
            return False, None

    # 4. Try DD-MM-YYYY
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", s)
    if m:
        day_str, month_str, year_str = m.groups()
        try:
            d = date(int(year_str), int(month_str), int(day_str))
            return True, d.isoformat()
        except ValueError:
            return False, None

    return False, None


def normalize_decimal(val: str | None) -> tuple[bool, float | None]:
    """
    Normalize decimal/currency value.
    Optionally prefixed by ₹, Rs, Rs. or INR (case-insensitive, optional space).
    Commas are thousands separators.
    Must be >= 0.
    Round to 2 places.
    Returns: (is_valid, rounded_float)
    """
    if val is None:
        return False, None
    s = str(val).strip()
    if not s:
        return False, None

    # Remove currency prefix
    # Matches ₹, Rs., Rs, INR at the start (case-insensitive) followed by optional spaces
    s_clean = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", s, flags=re.IGNORECASE).strip()

    # Remove commas
    s_clean = s_clean.replace(",", "")

    try:
        f = float(s_clean)
        if f < 0:
            return False, None
        return True, round(f, 2)
    except ValueError:
        return False, None


def normalize_enum(val: str | None, allowed_values: list[str]) -> tuple[bool, str | None]:
    """
    Normalize enum value.
    Compared case-insensitively against allowed values and stored lower-case.
    Returns: (is_valid, lowercase_enum_str)
    """
    if val is None:
        return False, None
    s = str(val).strip().lower()
    if not s:
        return False, None

    allowed_lower = [v.lower() for v in allowed_values]
    if s in allowed_lower:
        return True, s
    return False, None


def parse_timestamp(ts_str: str) -> datetime:
    """
    Parse timestamp string into timezone-aware datetime in UTC.
    Supported formats:
    - UTC: 2026-10-05T13:20:21Z
    - Offset: 2026-10-01T10:40:19+05:30
    - Naive: 2026-10-01 09:15:55 (naive means IST per spec)
    """
    s = ts_str.strip()
    if s.endswith("Z"):
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt
    if "+" in s or "-" in s[10:]:  # Has timezone offset
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc)

    # Naive timestamp -> IST (+05:30) per spec
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST_TZ)
    return dt.astimezone(timezone.utc)


def to_ist(dt: datetime) -> datetime:
    """Convert an aware datetime to Indian Standard Time (UTC+05:30)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ)


def format_iso_ist(dt: datetime | None) -> str:
    """
    Format aware datetime as ISO-8601 in IST, e.g. 2026-10-14T17:55:24+05:30.
    Returns empty string if dt is None.
    """
    if dt is None:
        return ""
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")

