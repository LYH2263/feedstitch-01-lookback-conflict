import json
from pathlib import Path

import pytest

from app.config import FIXTURES_DIR
from app.engines.merge_rules import (
    decide_merge,
    eligible_for_incremental,
    fields_conflict,
    within_lookback_window,
)
from app.engines.parse_feed import parse_fixture


def test_parse_json_alpha():
    items = parse_fixture(FIXTURES_DIR / "alpha_catalog.json", "json")
    assert len(items) == 3
    assert items[0]["business_key"] == "SKU-1001"
    assert items[0]["price"] == 129.0


def test_parse_jsonl_beta():
    items = parse_fixture(FIXTURES_DIR / "beta_offers.jsonl", "jsonl")
    assert len(items) == 3
    assert items[0]["business_key"] == "SKU-1001"
    assert items[0]["price"] == 99.0


def test_parse_xml_gamma():
    items = parse_fixture(FIXTURES_DIR / "gamma_shelf.xml", "xml")
    assert len(items) == 2
    assert items[1]["business_key"] == "SKU-3002"


def test_conflict_on_price():
    a = {"title": "Oak Desk Lamp", "price": 129.0, "category": "lighting", "source_revision": 3}
    b = {"title": "Oak Desk Lamp", "price": 99.0, "category": "lighting", "source_revision": 4}
    assert fields_conflict(a, b) is True
    assert decide_merge(a, {**b, "business_key": "SKU-1001", "event_ts": "t", "payload": {}}) == "conflict"


def test_upsert_higher_revision_same_fields():
    a = {
        "business_key": "SKU-1",
        "title": "X",
        "price": 10.0,
        "category": "c",
        "source_revision": 1,
        "event_ts": "t1",
        "payload": {},
    }
    b = {
        "business_key": "SKU-1",
        "title": "X",
        "price": 10.0,
        "category": "c",
        "source_revision": 2,
        "event_ts": "t2",
        "payload": {},
    }
    assert decide_merge(a, b) == "upsert"


def test_insert_when_missing():
    incoming = {
        "business_key": "SKU-9",
        "title": "Y",
        "price": 1.0,
        "category": "c",
        "source_revision": 1,
        "event_ts": "t",
        "payload": {},
    }
    assert decide_merge(None, incoming) == "insert"


def test_watermark_gate():
    assert eligible_for_incremental("2026-11-02T00:00:00Z", None) is True
    assert eligible_for_incremental("2026-11-02T00:00:00Z", "2026-11-01T00:00:00Z") is True
    assert eligible_for_incremental("2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z") is False
    assert eligible_for_incremental("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z") is False


def test_lookback_window_boundaries():
    wm = "2026-11-01T12:00:00Z"
    assert within_lookback_window("2026-11-01T11:30:00Z", wm, 1800) is True
    assert within_lookback_window("2026-11-01T10:00:00Z", wm, 1800) is False
    assert within_lookback_window("2026-11-01T12:05:00Z", wm, 1800) is False
    # lookback 0 disables the window entirely
    assert within_lookback_window(wm, wm, 0) is False


def test_lookback_only_rescues_newer_revision():
    wm = "2026-11-01T12:00:00Z"
    late = "2026-11-01T11:30:00Z"
    old = "2026-11-01T09:00:00Z"

    # late, in window, higher revision -> eligible (lookback backfill)
    assert eligible_for_incremental(
        late, wm, 3600, existing_revision=2, incoming_revision=3
    ) is True
    # late, in window, same/lower revision -> remains stale
    assert eligible_for_incremental(
        late, wm, 3600, existing_revision=3, incoming_revision=3
    ) is False
    assert eligible_for_incremental(
        late, wm, 3600, existing_revision=4, incoming_revision=3
    ) is False
    # late, in window, but nothing exists in canonical -> not an upsert
    assert eligible_for_incremental(
        late, wm, 3600, existing_revision=None, incoming_revision=3
    ) is False
    # late outside the window -> skipped regardless of revision
    assert eligible_for_incremental(
        old, wm, 3600, existing_revision=1, incoming_revision=9
    ) is False
    # neither watermark nor revision gate can be applied without an watermark
    assert eligible_for_incremental(old, None, 0) is True


def test_lookback_conflict_still_wins_over_upsert():
    existing = {
        "business_key": "SKU-1",
        "title": "X",
        "price": 10.0,
        "category": "c",
        "source_revision": 2,
        "event_ts": "t1",
        "payload": {},
    }
    incoming = {
        "business_key": "SKU-1",
        "title": "X",
        "price": 9.0,
        "category": "c",
        "source_revision": 3,
        "event_ts": "t0",
        "payload": {},
    }
    assert decide_merge(existing, incoming) == "conflict"



def test_parse_missing_field_raises(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"business_key": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        parse_fixture(p, "json")
