from __future__ import annotations

from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.merge_rules import (
    CONFLICT_DISPOSITIONS,
    SKIP_DISPOSITIONS,
    UPSERT_DISPOSITIONS,
    _is_after,
    decide_merge,
    within_lookback_window,
)
from app.engines.parse_feed import parse_fixture
from app.repositories import canonical as canon_repo
from app.repositories import runs as runs_repo
from app.repositories import sources as sources_repo


def preview_source(source_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        source = sources_repo.get_source(conn, source_id)
        if not source:
            raise ValueError("source not found")
        path = FIXTURES_DIR / source["fixture_path"]
        items = parse_fixture(path, source["format"])
        return {
            "source": source,
            "count": len(items),
            "items": items[:50],
        }
    finally:
        conn.close()


def _existing_view(existing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not existing:
        return None
    return {
        "business_key": existing["business_key"],
        "title": existing["title"],
        "price": existing["price"],
        "category": existing["category"],
        "event_ts": existing["event_ts"],
        "source_revision": existing["source_revision"],
        "payload": existing["payload"],
    }


def _disposition_summary(conn, run_id: int) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT disposition, COUNT(*) AS c
        FROM staging_rows WHERE run_id = ?
        GROUP BY disposition
        """,
        (run_id,),
    ).fetchall()
    return {r["disposition"]: int(r["c"]) for r in rows}


def run_ingest(source_id: int, mode: str = "incremental") -> dict[str, Any]:
    if mode not in ("incremental", "full"):
        raise ValueError("mode must be incremental or full")

    conn = connect()
    try:
        source = sources_repo.get_source(conn, source_id)
        if not source:
            raise ValueError("source not found")
        if not source["enabled"]:
            raise ValueError("source disabled")

        if mode == "full":
            # A full rebuild restarts the source from scratch; every fixture
            # row is replayed through the same merge rules below.
            sources_repo.set_watermark(conn, source_id, None)
            source["watermark_ts"] = None

        lookback_seconds = int(source.get("lookback_seconds") or 0)
        run_id = runs_repo.create_run(conn, source_id, mode)
        path = FIXTURES_DIR / source["fixture_path"]

        try:
            items = parse_fixture(path, source["format"])
        except Exception as exc:  # noqa: BLE001
            runs_repo.finish_run(
                conn,
                run_id,
                status="failed",
                parsed=0,
                upserted=0,
                conflicts=0,
                skipped=0,
                report={"error": str(exc)},
            )
            conn.commit()
            raise

        max_event_ts: str | None = source.get("watermark_ts")
        lookback_candidates = 0

        for item in items:
            watermark_ts = source.get("watermark_ts")
            is_late = bool(watermark_ts) and not _is_after(
                item["event_ts"], watermark_ts
            )

            existing = canon_repo.get_by_key(conn, item["business_key"])
            existing_view = _existing_view(existing)

            if is_late:
                # Late record: only the lookback window may rescue it, and
                # only when canonical already holds the key at a lower
                # revision.  A late event for a never-seen key is left for
                # a full replay (or a future in-order event).
                in_window = within_lookback_window(
                    item["event_ts"], watermark_ts, lookback_seconds
                )
                newer_revision = existing_view is not None and (
                    int(item["source_revision"])
                    > int(existing_view["source_revision"])
                )
                if not in_window or not newer_revision:
                    disposition = "skip_stale" if in_window else "skipped_watermark"
                    runs_repo.insert_staging(
                        conn,
                        run_id=run_id,
                        source_id=source_id,
                        item=item,
                        disposition=disposition,
                    )
                    # Late rows never advance the watermark; it is already
                    # at or beyond their event_ts.
                    continue
                lookback_candidates += 1
            decision = decide_merge(existing_view, item)

            if decision in ("insert", "upsert"):
                canon_repo.upsert_item(conn, item, source_id)
                if is_late:
                    disposition = f"lookback_{decision}"
                else:
                    disposition = decision
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition=disposition,
                )
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]
            elif decision == "conflict":
                runs_repo.insert_conflict(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    business_key=item["business_key"],
                    existing_payload=existing_view["payload"] if existing_view else {},
                    incoming_payload=item["payload"],
                    reason="non_key_field_mismatch",
                )
                # Quarantined rows are recorded for the run but are never
                # written to canonical.
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="quarantine",
                )
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]
            else:
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="skip_stale",
                )
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]

        if max_event_ts:
            sources_repo.set_watermark(conn, source_id, max_event_ts)

        # Counts are derived from the staging rows of this run so that the
        # run report and the Staging page can never drift apart.
        disposition_counts = _disposition_summary(conn, run_id)
        parsed_count = sum(disposition_counts.values())
        upserted_count = sum(
            c for d, c in disposition_counts.items() if d in UPSERT_DISPOSITIONS
        )
        conflict_count = sum(
            c for d, c in disposition_counts.items() if d in CONFLICT_DISPOSITIONS
        )
        skipped_count = sum(
            c for d, c in disposition_counts.items() if d in SKIP_DISPOSITIONS
        )
        conflict_rows_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM conflicts WHERE run_id = ?", (run_id,)
            ).fetchone()["c"]
        )
        canonical_count = canon_repo.count_items(conn)

        report = {
            "source_id": source_id,
            "mode": mode,
            "lookback_seconds": lookback_seconds,
            "parsed_count": parsed_count,
            "upserted_count": upserted_count,
            "conflict_count": conflict_count,
            "skipped_count": skipped_count,
            "canonical_count": canonical_count,
            "watermark_ts": max_event_ts,
            "lookback_candidate_count": lookback_candidates,
            "lookback_upserted_count": sum(
                c for d, c in disposition_counts.items()
                if d in ("lookback_insert", "lookback_upsert")
            ),
            "conflict_rows_count": conflict_rows_count,
            "disposition_counts": disposition_counts,
        }
        runs_repo.finish_run(
            conn,
            run_id,
            status="ok",
            parsed=parsed_count,
            upserted=upserted_count,
            conflicts=conflict_count,
            skipped=skipped_count,
            report=report,
        )
        conn.commit()
        run = runs_repo.get_run(conn, run_id)
        return run or report
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        sources = sources_repo.list_sources(conn)
        return {
            "source_count": len(sources),
            "enabled_sources": sum(1 for s in sources if s["enabled"]),
            "canonical_count": canon_repo.count_items(conn),
            "conflict_count": conn.execute("SELECT COUNT(*) c FROM conflicts").fetchone()["c"],
            "run_count": conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"],
            "staging_count": conn.execute("SELECT COUNT(*) c FROM staging_rows").fetchone()["c"],
        }
    finally:
        conn.close()
