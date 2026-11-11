"""Business-key merge / conflict decisions for Feedstitch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


NON_KEY_FIELDS = ("title", "price", "category")

# Disposition groups written to staging.  Keeping the mapping in one place
# makes the run report and the Staging page agree by construction.
UPSERT_DISPOSITIONS = frozenset({"insert", "upsert", "lookback_insert", "lookback_upsert"})
CONFLICT_DISPOSITIONS = frozenset({"quarantine"})
SKIP_DISPOSITIONS = frozenset({"skip_stale", "skipped_watermark"})


def non_key_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item[k] for k in NON_KEY_FIELDS}


def fields_conflict(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    return non_key_snapshot(existing) != non_key_snapshot(incoming)


def decide_merge(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> str:
    """Return one of: insert | upsert | conflict | skip_stale.

    Adjudication is independent of the watermark gate.  Once a record is
    eligible for (re-)processing the rules are:

    - no existing canonical row          → insert
    - non-key fields diverge             → conflict (never overwrite canonical)
    - same non-key, incoming rev higher  → upsert
    - otherwise                          → skip_stale
    """
    if existing is None:
        return "insert"
    if fields_conflict(existing, incoming):
        return "conflict"
    if int(incoming["source_revision"]) > int(existing["source_revision"]):
        return "upsert"
    return "skip_stale"


def _parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, treating a trailing ``Z`` as UTC."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_after(event_ts: str, watermark_ts: str) -> bool:
    event_dt = _parse_ts(event_ts)
    watermark_dt = _parse_ts(watermark_ts)
    if event_dt is not None and watermark_dt is not None:
        return event_dt > watermark_dt
    # Unknown/odd formats: fall back to the lexicographic behaviour the
    # original implementation relied on.
    return str(event_ts) > str(watermark_ts)


def within_lookback_window(
    event_ts: str,
    watermark_ts: str,
    lookback_seconds: int,
) -> bool:
    """True when event_ts is in [watermark - lookback, watermark].

    The window is only used for events that are *not* newer than the
    watermark.  A non-positive lookback means "no lookback window".
    """
    lookback = int(lookback_seconds or 0)
    if lookback <= 0:
        return False
    if _is_after(event_ts, watermark_ts):
        return False
    event_dt = _parse_ts(event_ts)
    watermark_dt = _parse_ts(watermark_ts)
    if event_dt is not None and watermark_dt is not None:
        return watermark_dt - timedelta(seconds=lookback) <= event_dt <= watermark_dt
    # Fallback for formats datetime cannot parse: we cannot compute a
    # window, so only an exact watermark hit is considered inside it.
    return str(event_ts) == str(watermark_ts)


def eligible_for_incremental(
    event_ts: str,
    watermark_ts: str | None,
    lookback_seconds: int = 0,
    *,
    existing_revision: int | None = None,
    incoming_revision: int | None = None,
) -> bool:
    """Watermark gate for incremental runs.

    - no watermark (first run / full reset) → eligible
    - event_ts strictly after watermark      → eligible
    - late event inside the lookback window  → eligible only when the
      incoming revision is higher than an existing canonical revision
    - any other late event                    → not eligible
    """
    if not watermark_ts:
        return True
    if _is_after(event_ts, watermark_ts):
        return True
    if not within_lookback_window(event_ts, watermark_ts, lookback_seconds):
        return False
    if incoming_revision is not None:
        if existing_revision is None:
            return False
        return int(incoming_revision) > int(existing_revision)
    return True
