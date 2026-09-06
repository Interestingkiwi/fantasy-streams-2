"""
Background-job dispatch for Fantasy Streams.

One call site - enqueue(func, *args, job_id=...) - that runs the work on an
RQ worker when REDIS_URL is set, and in a background daemon thread when it
isn't (local dev without Redis). job_id deduplicates: a job already queued
or running is not started again.

The league-sync freshness rules (skip if data is fresh, roster-only vs full)
belong in the Phase 2 trigger that calls enqueue(), not here.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import logging
import threading
from dataclasses import dataclass

from config import Config

log = logging.getLogger(__name__)

QUEUE_NAME = "default"
DEFAULT_TIMEOUT = 1800   # seconds; matches the old job_timeout
RESULT_TTL = 300

_ACTIVE_RQ_STATES = ("queued", "started", "deferred", "scheduled")


@dataclass
class JobHandle:
    backend: str   # "rq" | "inline"
    job_id: str
    status: str    # "queued" | "started" | "already_running"


# --- inline backend ---------------------------------------------------------
# Dedup state is per-process. That's fine: the inline backend is only used in
# single-process local dev. Multi-worker deploys set REDIS_URL and use RQ.

_inline_lock = threading.Lock()
_inline_running = set()


def _run_inline(func, job_id, args, kwargs):
    try:
        func(*args, **kwargs)
    except Exception:
        log.exception("Inline job %s failed.", job_id)
    finally:
        with _inline_lock:
            _inline_running.discard(job_id)


def _enqueue_inline(func, args, kwargs, job_id):
    with _inline_lock:
        if job_id in _inline_running:
            return JobHandle("inline", job_id, "already_running")
        _inline_running.add(job_id)

    threading.Thread(
        target=_run_inline,
        args=(func, job_id, args, kwargs),
        name=f"job:{job_id}",
        daemon=True,
    ).start()
    return JobHandle("inline", job_id, "started")


# --- RQ backend -----------------------------------------------------------

def _get_queue():
    from redis import Redis
    from rq import Queue

    return Queue(QUEUE_NAME, connection=Redis.from_url(Config.REDIS_URL))


def _enqueue_rq(func, args, kwargs, job_id, timeout):
    from rq.job import Job
    from rq.exceptions import NoSuchJobError

    queue = _get_queue()
    try:
        existing = Job.fetch(job_id, connection=queue.connection)
        if existing.get_status(refresh=True) in _ACTIVE_RQ_STATES:
            return JobHandle("rq", job_id, "already_running")
    except NoSuchJobError:
        pass

    queue.enqueue_call(
        func=func,
        args=args,
        kwargs=kwargs,
        job_id=job_id,
        timeout=timeout,
        result_ttl=RESULT_TTL,
    )
    return JobHandle("rq", job_id, "queued")


# --- public API ---------------------------------------------------------

def enqueue(func, *args, job_id, timeout=DEFAULT_TIMEOUT, **kwargs):
    """
    Run func(*args, **kwargs) out of band. Uses the RQ worker if REDIS_URL is
    set, otherwise a background daemon thread. job_id is required and dedupes.
    func must be importable by module path for the RQ backend to pickle it.
    """
    if Config.REDIS_URL:
        return _enqueue_rq(func, args, kwargs, job_id, timeout)
    return _enqueue_inline(func, args, kwargs, job_id)


def job_status(job_id):
    """Best-effort status string for either backend; 'unknown' if not found."""
    if Config.REDIS_URL:
        from rq.job import Job
        from rq.exceptions import NoSuchJobError

        try:
            job = Job.fetch(job_id, connection=_get_queue().connection)
            return job.get_status(refresh=True)
        except NoSuchJobError:
            return "unknown"

    with _inline_lock:
        return "started" if job_id in _inline_running else "unknown"
