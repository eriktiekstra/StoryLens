"""Detached process entry point for application jobs (never an MCP server)."""

import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from .access import PathPolicy
from .jobs import (HEARTBEAT_SECONDS, LEASE_SECONDS, JobCancelled, _Store,
                   _config_from_document, _now, _summary)
from .service import Analyzer


def _guard_catalog_writes(catalog, store, job_id, attempt, token, lost):
    """Serialize each short catalog commit with lease takeover, never extraction."""
    for name in ("observe", "save", "fail"):
        method = getattr(catalog, name)

        def guarded(*args, _method=method, **kwargs):
            with store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = store.row(connection, job_id)
                if (lost.is_set() or row["token"] != token or row["attempt"] != attempt
                        or row["status"] != "running" or row["lease_until"] < time.time()):
                    raise JobCancelled()
                # Resume/cancel cannot change ownership until this commit returns.
                return _method(*args, **kwargs)

        setattr(catalog, name, guarded)


def _claim(store, job_id, attempt):
    """Claim FIFO by queue-entry time (updated_at, id), never race the slot.

    Queued workers renew only their own leases. Expired or cancelled
    predecessors leave the queue, so an absent worker cannot starve successors.
    """
    token = uuid.uuid4().hex
    while True:
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            store.expire(connection)
            row = store.row(connection, job_id)
            if row["attempt"] != attempt or row["status"] != "queued":
                return None
            first = connection.execute("""SELECT id FROM jobs WHERE status='queued'
                ORDER BY updated_at,id LIMIT 1""").fetchone()
            if (first["id"] == job_id and
                    connection.execute("SELECT 1 FROM jobs WHERE status IN ('running','cancel_requested')").fetchone() is None):
                now = _now()
                connection.execute("""UPDATE jobs SET status='running',token=?,started_at=?,
                    updated_at=?,heartbeat_at=?,lease_until=?,worker_pid=?
                    WHERE id=? AND attempt=? AND status='queued'""",
                    (token, now, now, now, time.time() + LEASE_SECONDS, os.getpid(), job_id, attempt))
                return token
            connection.execute("UPDATE jobs SET heartbeat_at=?,lease_until=? WHERE id=? AND attempt=?",
                               (_now(), time.time() + LEASE_SECONDS, job_id, attempt))
        time.sleep(0.2)


def _finish(store, job_id, token, status, error=None, summary=None):
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        store.expire(connection)
        row = store.row(connection, job_id)
        if row["token"] != token or row["status"] not in {"running", "cancel_requested"}:
            return
        if row["status"] == "cancel_requested":
            status = "cancelled"
        now = _now()
        connection.execute("""UPDATE jobs SET status=?,error=?,updated_at=?,finished_at=?,
            summary=COALESCE(?,summary),token=NULL,lease_until=NULL WHERE id=? AND token=?""",
            (status, str(error)[:4000] if error is not None else None, now, now,
             json.dumps(_summary(summary)) if summary is not None else None, job_id, token))


def run(path, job_id, attempt):
    store = _Store(path)
    token = _claim(store, job_id, attempt)
    if token is None:
        return
    stop, lost = threading.Event(), threading.Event()

    def heartbeat():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                with store.connect() as connection:
                    changed = connection.execute("""UPDATE jobs SET heartbeat_at=?,lease_until=?
                        WHERE id=? AND attempt=? AND token=?
                        AND status IN ('running','cancel_requested') AND lease_until>=?""",
                        (_now(), time.time() + LEASE_SECONDS, job_id, attempt, token, time.time())).rowcount
                if not changed:
                    lost.set()
                    return
            except Exception:
                # Losing durable ownership is a stop, never license to keep writing.
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with store.connect() as connection:
            row = store.row(connection, job_id)
        config = _config_from_document(json.loads(row["config"]))
        if config.database.with_suffix(".jobs.sqlite3").resolve() != store.path.resolve():
            raise ValueError("Job configuration does not match its catalog")
        policy = PathPolicy(json.loads(row["roots"]))
        root = Path(row["root"])
        if policy.authorize(root) != root:
            raise ValueError("Job root changed since submission")
        request = json.loads(row["request"])
        # Completed stages belong to the catalog. A resumed force request must reuse them.
        if attempt > 1:
            request["force"] = False

        def checkpoint(summary):
            bounded = _summary(summary)
            with store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = store.row(connection, job_id)
                if (lost.is_set() or current["token"] != token or current["attempt"] != attempt
                        or current["status"] not in {"running", "cancel_requested"}
                        or current["lease_until"] < time.time()):
                    raise JobCancelled()
                connection.execute("UPDATE jobs SET summary=?,updated_at=? WHERE id=? AND token=?",
                                   (json.dumps(bounded), _now(), job_id, token))
                cancelled = current["status"] == "cancel_requested"
            if cancelled:
                raise JobCancelled()
            if policy.authorize(root) != root:
                raise ValueError("Job root changed during execution")

        with Analyzer(config) as analyzer:
            _guard_catalog_writes(analyzer.catalog, store, job_id, attempt, token, lost)
            summary = analyzer.scan(root, checkpoint=checkpoint, **request)
        failed = summary.get("failed", 0)
        _finish(store, job_id, token, "failed" if failed else "succeeded",
                error=f"Analysis completed with {failed} failed files" if failed else None,
                summary=summary)
    except JobCancelled:
        _finish(store, job_id, token, "interrupted" if lost.is_set() else "cancelled",
                error="Worker heartbeat ownership lost" if lost.is_set() else None)
    except Exception as error:
        _finish(store, job_id, token, "failed", error=error)
    except (KeyboardInterrupt, SystemExit) as error:
        _finish(store, job_id, token, "interrupted", error=error)
    finally:
        stop.set()
        thread.join(timeout=HEARTBEAT_SECONDS + 1)


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: python -m storylens.job_worker STORE JOB_ID ATTEMPT")
    run(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))


if __name__ == "__main__":
    main()
