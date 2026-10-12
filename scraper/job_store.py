"""
Thread-safe, job-isolated status store for live progress tracking.

Background workers must not mutate Streamlit session state directly.
Instead, every job's status, metrics, logs, cancellation flag, and outputs
are isolated here by job ID. The UI polls this store on each rerun.
"""

import threading
import time
from typing import Any, Dict, List, Optional

# Distinct terminal and in-flight states used across engine + UI.
STATE_QUEUED = "Queued"
STATE_FETCHING = "Fetching source"
STATE_DISCOVERING = "Discovering products"
STATE_EXTRACTING = "Extracting products"
STATE_EXPORTING = "Exporting"
STATE_COMPLETED = "Completed"
STATE_COMPLETED_WITH_ERRORS = "Completed with errors"
STATE_CANCELLED = "Cancelled"
STATE_FAILED = "Failed"

TERMINAL_STATES = {
    STATE_COMPLETED,
    STATE_COMPLETED_WITH_ERRORS,
    STATE_CANCELLED,
    STATE_FAILED,
}


def _default_status(job_id: str = "", url: str = "") -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "url": url,
        "state": STATE_QUEUED,
        "status": "Queued",
        "message": "",
        "error": "",
        "progress_count": 0,
        "total_target": 0,
        "current_product": "",
        "extracted": 0,
        "failed": 0,
        "duplicates": 0,
        "discovered": 0,
        "elapsed_seconds": 0.0,
        "logs": [],
        "updated_at": time.time(),
    }


class JobStatusStore:
    """Process-wide registry of per-job status dicts, guarded by a lock."""

    _lock = threading.Lock()
    _jobs: Dict[str, Dict[str, Any]] = {}
    _cancel_events: Dict[str, threading.Event] = {}

    @classmethod
    def create_job(cls, job_id: str, url: str = "") -> Dict[str, Any]:
        with cls._lock:
            cls._jobs[job_id] = _default_status(job_id=job_id, url=url)
            if job_id not in cls._cancel_events:
                cls._cancel_events[job_id] = threading.Event()
            return dict(cls._jobs[job_id])

    @classmethod
    def update(cls, job_id: str, **fields: Any) -> None:
        with cls._lock:
            job = cls._jobs.setdefault(job_id, _default_status(job_id=job_id))
            job.update(fields)
            job["updated_at"] = time.time()

    @classmethod
    def append_log(cls, job_id: str, line: str, limit: int = 200) -> None:
        with cls._lock:
            job = cls._jobs.setdefault(job_id, _default_status(job_id=job_id))
            logs: List[str] = job.setdefault("logs", [])
            logs.append(line)
            if len(logs) > limit:
                del logs[: len(logs) - limit]
            job["updated_at"] = time.time()

    @classmethod
    def get(cls, job_id: str) -> Dict[str, Any]:
        with cls._lock:
            job = cls._jobs.get(job_id)
            if job is None:
                return _default_status(job_id=job_id)
            snapshot = dict(job)
            snapshot["logs"] = list(job.get("logs", []))
            return snapshot

    @classmethod
    def cancel_event(cls, job_id: str) -> threading.Event:
        with cls._lock:
            return cls._cancel_events.setdefault(job_id, threading.Event())

    @classmethod
    def request_cancel(cls, job_id: str) -> None:
        with cls._lock:
            cls._cancel_events.setdefault(job_id, threading.Event()).set()
            job = cls._jobs.get(job_id)
            if job is not None and job.get("state") not in TERMINAL_STATES:
                job["status"] = "Stop requested — finishing current item..."
                job["updated_at"] = time.time()

    @classmethod
    def is_cancel_requested(cls, job_id: str) -> bool:
        with cls._lock:
            ev = cls._cancel_events.get(job_id)
            return bool(ev and ev.is_set())

    @classmethod
    def clear(cls, job_id: str) -> None:
        with cls._lock:
            cls._jobs.pop(job_id, None)
            cls._cancel_events.pop(job_id, None)

    @classmethod
    def is_terminal(cls, job_id: str) -> bool:
        with cls._lock:
            job = cls._jobs.get(job_id)
            return bool(job and job.get("state") in TERMINAL_STATES)
