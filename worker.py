"""
RQ worker entry point for Fantasy Streams.

Run as its own always-on process (`python worker.py`) alongside the web
app when REDIS_URL is set. Not needed for local dev without Redis - there
jobs.enqueue() falls back to an in-process thread.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import logging
import os
import platform

from redis import Redis
from rq import Queue, SimpleWorker, Worker

from config import Config
from jobs import QUEUE_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

_WORKER_CLASSES = {"Worker": Worker, "SimpleWorker": SimpleWorker}


def main():
    if not Config.REDIS_URL:
        raise SystemExit("REDIS_URL is not set - nothing for the worker to connect to.")

    conn = Redis.from_url(Config.REDIS_URL)

    # RQ's default Worker forks per job, which POSIX-only. SimpleWorker runs
    # jobs in-process; it's the only option on Windows. Override on Linux with
    # WORKER_CLASS=Worker for per-job memory isolation.
    default_cls = "SimpleWorker" if platform.system() == "Windows" else "Worker"
    worker_cls = _WORKER_CLASSES[os.getenv("WORKER_CLASS", default_cls)]

    worker = worker_cls([Queue(QUEUE_NAME, connection=conn)], connection=conn)
    log.info("Starting %s on queue '%s'.", worker_cls.__name__, QUEUE_NAME)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
