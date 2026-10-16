"""Private, SQLite-backed detached jobs; no transport or broker dependency."""

from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from .access import PathPolicy
from .config import Config


LEASE_SECONDS = 30.0
HEARTBEAT_SECONDS = 2.0


class JobCancelled(BaseException):
    """Cooperative stop that must escape per-media error isolation."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _summary(value):
    """Bound persisted output without discarding counters or stage failures."""
    if value is None:
        return None
    result = {key: value[key] for key in (
        "schema_version", "root", "discovered", "indexed", "analyzed", "cache_hits", "failed")
        if key in value}
    errors = value.get("errors", [])
    result["errors"] = [{"path": str(error.get("path", ""))[:1024],
                         "stage": str(error.get("stage", ""))[:128],
                         "error": str(error.get("error", ""))[:1000]}
                        for error in errors[:100]]
    result["errors_truncated"] = bool(value.get("errors_truncated") or len(errors) > 100)
    result["stages"] = {str(key)[:128]: {counter: int(counts.get(counter, 0))
                         for counter in ("analyzed", "cache_hits", "failed")}
                        for key, counts in list(value.get("stages", {}).items())[:32]}
    return result


def _worker_environment():
    """Retain runtime discovery/cache settings, never ambient provider credentials."""
    names = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME",
             "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
             "PYTHONPATH", "PYTHONHOME", "PYTHONUTF8", "PYTHONIOENCODING",
             "PYTHONDONTWRITEBYTECODE", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
             "DYLD_FALLBACK_LIBRARY_PATH", "LANG", "LANGUAGE", "TZ",
             "XDG_CACHE_HOME", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE",
             "TRANSFORMERS_CACHE", "TORCH_HOME", "CUDA_VISIBLE_DEVICES",
             "NVIDIA_VISIBLE_DEVICES", "CUDA_HOME", "CUDA_PATH", "OMP_NUM_THREADS",
             "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS"}
    return {key: value for key, value in os.environ.items()
            if key.upper() in names or key.upper().startswith("LC_")}


def _launch_windows(command, environment, directory):
    """Ask local WMI to launch outside the MCP host's kill-on-close Job Object.

    DETACHED_PROCESS alone still inherits a Windows Job Object. WMI owns/reaps
    the independent worker; no transport handles or caller credentials are sent.
    The fixed program reads JSON from a private environment value, never code.
    """
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = r"""
$ErrorActionPreference = 'Stop'
$request = $env:STORYLENS_WORKER_LAUNCH | ConvertFrom-Json
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
    # CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    CreateFlags = [uint32]16777736
    ShowWindow = [uint16]0
    EnvironmentVariables = [string[]]$request.environment
}
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = [string]$request.command_line
    CurrentDirectory = [string]$request.directory
    ProcessStartupInformation = $startup
}
if ($result.ReturnValue -ne 0) { throw "WMI process creation failed: $($result.ReturnValue)" }
$result.ProcessId | ConvertTo-Json -Compress
"""
    request = json.dumps({"command_line": subprocess.list2cmdline(command),
                          "directory": str(directory),
                          "environment": [f"{key}={value}" for key, value in environment.items()]})
    try:
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL, text=True, capture_output=True,
            env=dict(environment, STORYLENS_WORKER_LAUNCH=request),
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
        if result.returncode or not isinstance(json.loads(result.stdout), int):
            raise ValueError(result.stderr[-2000:] or "Missing WMI process ID")
    except subprocess.TimeoutExpired as error:
        details = (error.stderr or b"")
        if isinstance(details, bytes):
            details = details.decode("utf-8", "replace")
        raise OSError("Windows worker launch exceeded its 20-second timeout: " + details[-2000:]) from error
    except (OSError, ValueError) as error:
        raise OSError("Cannot launch independent Windows worker through local PowerShell/WMI: " + str(error)[:2000]) from error


def _config_document(config):
    return {field.name: str(value) if isinstance(value, Path) else value
            for field in fields(config) for value in [getattr(config, field.name)]}


def _config_from_document(document):
    values = dict(document)
    values["database"] = Path(values["database"])
    values["exclusions"] = tuple(values.get("exclusions", ()))
    return Config(**values)


class _Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Creating before SQLite opens it avoids a world-readable creation window.
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        if os.name != "nt":
            self.path.chmod(0o600)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported jobs schema {version}; upgrade StoryLens")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, root TEXT NOT NULL, config TEXT NOT NULL,
                    roots TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT, heartbeat_at TEXT,
                    lease_until REAL, attempt INTEGER NOT NULL, token TEXT,
                    summary TEXT, error TEXT, worker_pid INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_job
                    ON jobs((1)) WHERE status IN ('running', 'cancel_requested');
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def expire(connection):
        now = _now()
        connection.execute("""UPDATE jobs SET status='interrupted', updated_at=?,
            finished_at=?, token=NULL, error='Worker lease expired; explicit resume required'
            WHERE status IN ('queued','running','cancel_requested') AND lease_until < ?""",
                           (now, now, time.time()))

    @staticmethod
    def row(connection, job_id):
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown job: " + str(job_id))
        return row

    @staticmethod
    def document(row):
        summary = json.loads(row["summary"]) if row["summary"] else None
        return {"schema_version": "1.0", "id": row["id"], "root": row["root"],
                "request": json.loads(row["request"]), "status": row["status"],
                **{name: row[name] for name in ("created_at", "updated_at", "started_at",
                   "finished_at", "heartbeat_at", "attempt")},
                "progress": {key: summary.get(key, 0) if summary else 0
                             for key in ("discovered", "indexed", "analyzed", "cache_hits", "failed")},
                "summary": summary, "error": row["error"]}


class JobManager:
    def __init__(self, config: Config, roots):
        self.config = replace(config, database=config.database.expanduser().resolve())
        self.policy = PathPolicy(roots)
        self.store = _Store(self.config.database.with_suffix(".jobs.sqlite3"))

    def _authorize(self, row):
        if str(self.policy.authorize(Path(row["root"]))) != row["root"]:
            raise ValueError("Job root changed since submission")

    def start(self, path, *, analyze=True, stage="all", depth=None, force=False):
        root = self.policy.authorize(Path(path))
        if type(analyze) is not bool or type(force) is not bool:
            raise ValueError("analyze and force must be booleans")
        if stage not in {"metadata", "technical", "semantic", "transcript", "all"}:
            raise ValueError("stage must be metadata, technical, semantic, transcript or all")
        if depth not in (None, "fast", "standard", "deep"):
            raise ValueError("depth must be fast, standard or deep")
        if depth is not None and stage not in {"semantic", "all"}:
            raise ValueError("depth applies only to semantic or all stages")
        if analyze and stage == "transcript" and not self.config.audio_transcription_model:
            raise ValueError("audio_transcription_model is required for transcript stage")
        request = {"analyze": analyze, "stage": stage, "depth": depth, "force": force}
        job_id, now = uuid.uuid4().hex, _now()
        with self.store.connect() as connection:
            self.store.expire(connection)
            connection.execute("""INSERT INTO jobs
                (id,root,config,roots,request,status,created_at,updated_at,lease_until,attempt)
                VALUES(?,?,?,?,?,'queued',?,?,?,1)""",
                (job_id, str(root), json.dumps(_config_document(self.config)),
                 json.dumps([str(root) for root in self.policy.roots]), json.dumps(request),
                 now, now, time.time() + LEASE_SECONDS))
        self._launch(job_id, 1)
        return self.status(job_id)

    def _launch(self, job_id, attempt):
        command = [sys.executable, "-m", "storylens.job_worker",
                   str(self.store.path), job_id, str(attempt)]
        environment = _worker_environment()
        try:
            if os.name == "nt":
                _launch_windows(command, environment, self.store.path.parent)
            else:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                    env=environment, start_new_session=True)
                # Reap without waiting for work or retaining transport pipes.
                threading.Thread(target=process.wait, daemon=True).start()
        except OSError as error:
            with self.store.connect() as connection:
                connection.execute("""UPDATE jobs SET status='failed',error=?,finished_at=?,updated_at=?
                    WHERE id=? AND attempt=? AND status='queued'""",
                    (str(error)[:4000], _now(), _now(), job_id, attempt))
            raise

    def status(self, job_id):
        with self.store.connect() as connection:
            row = self.store.row(connection, job_id)
            self._authorize(row)
            self.store.expire(connection)
            return self.store.document(self.store.row(connection, job_id))

    def result(self, job_id):
        return self.status(job_id)

    def cancel(self, job_id):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self.store.row(connection, job_id)
            self._authorize(row)
            self.store.expire(connection)
            row = self.store.row(connection, job_id)
            now = _now()
            if row["status"] == "queued":
                connection.execute("""UPDATE jobs SET status='cancelled',finished_at=?,updated_at=?,
                    token=NULL WHERE id=?""", (now, now, job_id))
            elif row["status"] == "running":
                connection.execute("UPDATE jobs SET status='cancel_requested',updated_at=? WHERE id=?",
                                   (now, job_id))
            return self.store.document(self.store.row(connection, job_id))

    def resume(self, job_id):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self.store.row(connection, job_id)
            self._authorize(row)
            self.store.expire(connection)
            row = self.store.row(connection, job_id)
            if row["status"] not in {"interrupted", "cancelled", "failed"}:
                raise ValueError("Only interrupted, cancelled or failed jobs may be resumed")
            attempt = row["attempt"] + 1
            connection.execute("""UPDATE jobs SET status='queued',attempt=?,token=NULL,
                updated_at=?,started_at=NULL,finished_at=NULL,heartbeat_at=NULL,
                lease_until=?,error=NULL,worker_pid=NULL WHERE id=?""",
                (attempt, _now(), time.time() + LEASE_SECONDS, job_id))
        self._launch(job_id, attempt)
        return self.status(job_id)

    def list(self, *, limit=50, after=""):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if not isinstance(after, str) or (after and (len(after) != 32 or any(c not in "0123456789abcdef" for c in after))):
            raise ValueError("Invalid job cursor")
        with self.store.connect() as connection:
            self.store.expire(connection)
            # Filter before paging so another collection cannot leak via cursors.
            roots = [str(root) for root in self.policy.roots]
            rows = connection.execute("SELECT * FROM jobs WHERE id>? ORDER BY id", (after,))
            items = []
            for row in rows:
                if not any(Path(row["root"]).is_relative_to(Path(root)) for root in roots):
                    continue
                self._authorize(row)
                items.append(self.store.document(row))
                if len(items) > limit:
                    break
        return {"schema_version": "1.0", "items": items[:limit],
                "next_cursor": items[limit - 1]["id"] if len(items) > limit else None}
