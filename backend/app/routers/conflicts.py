from fastapi import APIRouter, Query

from app.db import connect
from app.repositories import runs as runs_repo

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts")
def list_conflicts(run_id: int | None = Query(default=None)):
    conn = connect()
    try:
        return runs_repo.list_conflicts(conn, run_id)
    finally:
        conn.close()
