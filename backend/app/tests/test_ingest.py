"""End-to-end ingest tests with a temporary SQLite database."""

from __future__ import annotations

import pytest

from app import db, seed
from app.repositories import canonical as canon_repo
from app.repositories import runs as runs_repo
from app.repositories import sources as sources_repo
from app.services.ingest_service import run_ingest


def _canonical_item(key: str, title: str, price: float, category: str, rev: int) -> dict:
    payload = {
        "business_key": key,
        "title": title,
        "price": price,
        "category": category,
        "event_ts": "2026-10-31T00:00:00Z",
        "source_revision": rev,
    }
    return {
        "business_key": key,
        "title": title,
        "price": price,
        "category": category,
        "event_ts": payload["event_ts"],
        "source_revision": rev,
        "payload": payload,
    }


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    seed.init_db()
    conn = db.connect()
    # Canonical already holds Alpha's SKU-1001 and a lower-revision copy of
    # Beta's SKU-2002 (same non-key fields).
    canon_repo.upsert_item(conn, _canonical_item("SKU-1001", "Oak Desk Lamp", 129.0, "lighting", 3), 1)
    canon_repo.upsert_item(conn, _canonical_item("SKU-2002", "Stone Coaster", 18.0, "kitchen", 0), 2)
    # Beta's watermark is ahead of the fixture rows, so only lookback can
    # rescue the late records.
    sources_repo.set_watermark(conn, 2, "2026-11-01T12:05:00Z")
    sources_repo.update_lookback(conn, 2, 20000)
    conn.commit()
    yield conn
    conn.close()


def test_incremental_lookback_backfill_and_conflict(temp_db):
    run = run_ingest(2, "incremental")
    report = run["report"]

    assert report["parsed_count"] == 3
    assert report["upserted_count"] == 1  # SKU-2002 lookback upsert
    assert report["conflict_count"] == 1  # SKU-1001 price diverges
    assert report["skipped_count"] == 1  # SKU-2001 late, never seen before
    assert report["disposition_counts"] == {
        "quarantine": 1,
        "lookback_upsert": 1,
        "skip_stale": 1,
    }
    assert report["lookback_candidate_count"] == 2
    assert report["lookback_upserted_count"] == 1
    assert report["watermark_ts"] == "2026-11-01T12:05:00Z"

    # Report counts come from the staging rows of this run.
    staging = runs_repo.list_staging(temp_db, run["id"])
    assert len(staging) == report["parsed_count"]

    # Canonical count in the report matches the Canonical list.
    assert report["canonical_count"] == len(canon_repo.list_items(temp_db))
    assert report["canonical_count"] == 2

    # The conflicting item was quarantined, never written to canonical.
    assert canon_repo.get_by_key(temp_db, "SKU-1001")["price"] == 129.0
    assert len(runs_repo.list_conflicts(temp_db, run["id"])) == report["conflict_count"]

    # Late higher-revision matching row was upserted through lookback.
    assert canon_repo.get_by_key(temp_db, "SKU-2002")["source_revision"] == 1

    # The late never-seen key stays out of canonical during incremental.
    assert canon_repo.get_by_key(temp_db, "SKU-2001") is None


def test_full_replay_keeps_conflict_and_skips_stale(temp_db):
    run_ingest(2, "incremental")

    full = run_ingest(2, "full")
    report = full["report"]

    assert report["parsed_count"] == 3
    assert report["upserted_count"] == 1  # SKU-2001 is backfilled by the full replay
    assert report["conflict_count"] == 1
    assert report["skipped_count"] == 1
    assert report["disposition_counts"] == {
        "insert": 1,
        "quarantine": 1,
        "skip_stale": 1,
    }
    assert report["watermark_ts"] == "2026-11-01T12:05:00Z"

    # The previously quarantined conflict is re-produced and canonical is
    # untouched by the full replay.
    assert canon_repo.get_by_key(temp_db, "SKU-1001")["price"] == 129.0
    assert len(runs_repo.list_conflicts(temp_db, full["id"])) == 1
    assert canon_repo.get_by_key(temp_db, "SKU-2001") is not None
    assert report["canonical_count"] == len(canon_repo.list_items(temp_db))
    assert report["canonical_count"] == 3
