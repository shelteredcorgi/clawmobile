"""SMS and 2FA interception module.

Queries the macOS Messages database (chat.db) for SMS messages synced
from the tethered iPhone and extracts verification codes.

macOS syncs iMessages and SMS (via iPhone relay) to chat.db over USB/iCloud.
No additional tools required — uses Python's built-in sqlite3.

Date format note:
  chat.db stores message dates as CoreData timestamps (seconds or nanoseconds
  since 2001-01-01 00:00:00 UTC). macOS Big Sur (11.0) switched to nanoseconds.
  This module detects the format automatically.

Permissions note:
  Terminal (or the Python process) must have Full Disk Access granted in
  System Settings → Privacy & Security → Full Disk Access.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from ifarm.exceptions import SMSError
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)

# Seconds between Unix epoch (1970-01-01) and Apple CoreData epoch (2001-01-01)
_APPLE_EPOCH_OFFSET = 978_307_200

# Threshold: if a date value is larger than this it's in nanoseconds, not seconds.
# 1e13 ≈ year 2317 in seconds, but only year 2001 + 0.3ms in nanoseconds — safe cutoff.
_NS_THRESHOLD = 1_000_000_000_000  # 1 trillion

# Default location of the macOS Messages database
_DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

# Ordered patterns for common 2FA code formats (most → least specific)
_CODE_PATTERNS = [
    re.compile(r"\b([A-Z]{2,4}[-\s]?\d{4,8})\b"),  # Alphanumeric: AB-123456
    re.compile(r"\b(\d{6,8})\b"),                   # 6-8 digit numeric
    re.compile(r"\b(\d{4})\b"),                     # 4-digit numeric (PIN)
]


def fetch_recent_sms(
    since_seconds: int = 60,
    db_path: Path | str | None = None,
) -> list[dict]:
    """Query chat.db for inbound SMS messages received in the last N seconds.

    Opens chat.db in read-only mode (safe — does not lock the Messages app).

    Args:
        since_seconds: Time window to search. Default 60 seconds.
        db_path: Override path to chat.db. Defaults to
            ~/Library/Messages/chat.db.

    Returns:
        List of message dicts ordered newest-first, each with keys:
            id (int), text (str), date (int), sender (str | None)

    Raises:
        SMSError: If chat.db is not found, cannot be read, or lacks
            Full Disk Access permission.
    """
    db = Path(db_path) if db_path else _DEFAULT_CHAT_DB

    if not db.exists():
        raise SMSError(
            f"chat.db not found at {db}. "
            "Ensure Messages is enabled and iCloud Messages sync is active, "
            "or the iPhone is paired and messages are being relayed."
        )

    cutoff_unix = time.time() - since_seconds

    # Compute cutoff in both Apple date formats
    cutoff_apple_s = cutoff_unix - _APPLE_EPOCH_OFFSET
    cutoff_apple_ns = cutoff_apple_s * 1_000_000_000

    conn = None
    try:
        # Open read-only via URI — avoids write-lock contention with Messages.app
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                m.rowid          AS id,
                m.text           AS text,
                m.date           AS date,
                h.id             AS sender
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.rowid
            WHERE
                m.is_from_me = 0
                AND m.text IS NOT NULL
                AND length(m.text) > 0
                AND (
                    -- nanosecond format (macOS 11+)
                    (m.date > :cutoff_ns AND m.date > :ns_threshold)
                    OR
                    -- second format (older macOS)
                    (m.date <= :ns_threshold AND m.date > :cutoff_s)
                )
            ORDER BY m.date DESC
            LIMIT 50
            """,
            {
                "cutoff_ns": cutoff_apple_ns,
                "cutoff_s": cutoff_apple_s,
                "ns_threshold": _NS_THRESHOLD,
            },
        )

        rows = cursor.fetchall()
        messages = [
            {
                "id": row["id"],
                "text": row["text"],
                "date": row["date"],
                "sender": row["sender"],
            }
            for row in rows
        ]

        _log.info(
            "Fetched recent SMS",
            extra={"count": len(messages), "since_seconds": since_seconds},
        )
        return messages

    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        err = str(e)
        if "unable to open" in err or "authorization" in err.lower():
            raise SMSError(
                f"Cannot open chat.db: {err}. "
                "Grant Full Disk Access to Terminal in "
                "System Settings → Privacy & Security → Full Disk Access."
            ) from e
        raise SMSError(f"chat.db query failed: {err}") from e
    finally:
        if conn:
            conn.close()


def extract_code(
    messages: list[dict],
    pattern: re.Pattern | None = None,
) -> str | None:
    """Parse a list of SMS messages and return the first 2FA/OTP code found.

    Tries patterns in order of specificity. The first match across all
    messages wins.

    Args:
        messages: List of message dicts as returned by fetch_recent_sms().
        pattern: Override regex pattern. If None, tries _CODE_PATTERNS
            in order.

    Returns:
        Extracted code string, or None if no code is found.
    """
    patterns = [pattern] if pattern else _CODE_PATTERNS
    for msg in messages:
        text = msg.get("text") or ""
        for pat in patterns:
            m = pat.search(text)
            if m:
                code = m.group(1).strip()
                _log.info(
                    "Extracted 2FA code",
                    extra={"sender": msg.get("sender"), "pattern": pat.pattern},
                )
                return code
    return None


def fetch_recent_2fa(
    keyword: str = "code",
    since_seconds: int = 60,
    db_path: Path | str | None = None,
) -> str | None:
    """Fetch messages and extract the most recent 2FA/OTP code.

    Convenience wrapper that combines fetch_recent_sms() and extract_code().

    Args:
        keyword: Filter messages containing this word before code extraction.
            Case-insensitive. Pass empty string "" to skip filtering.
            Common values: "code", "verify", "otp", "pin".
        since_seconds: Time window for fetch_recent_sms(). Default 60.
        db_path: Override path to chat.db.

    Returns:
        Extracted code string, or None if not found.

    Raises:
        SMSError: If chat.db cannot be accessed.
    """
    messages = fetch_recent_sms(since_seconds=since_seconds, db_path=db_path)

    if keyword:
        kw = keyword.lower()
        messages = [
            m for m in messages
            if kw in (m.get("text") or "").lower()
        ]
        _log.info(
            "Filtered messages by keyword",
            extra={"keyword": keyword, "remaining": len(messages)},
        )

    return extract_code(messages)
