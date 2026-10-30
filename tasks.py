"""Celery tasks. Each runs async Coldbrew code on its own event loop."""

import asyncio
import logging
from dataclasses import asdict

import jobs
from celery_app import app
from config import get_settings

log = logging.getLogger("coldbrew.tasks")

LOCK_KEY = "coldbrew:follow-ups"


def _lock():
    """A Redis lock so two checks never overlap (a slow run when beat fires again, or several workers).
    None when the broker isn't Redis, as in tests and the in-memory broker."""
    settings = get_settings()
    if not settings.broker_url.startswith(("redis://", "rediss://")):
        return None
    import redis

    client = redis.Redis.from_url(settings.broker_url)
    # Released when the check ends; expires on its own if a worker dies mid-run.
    return client.lock(LOCK_KEY, timeout=settings.followup_check_seconds, blocking=False)


@app.task(name="coldbrew.check_follow_ups")
def check_follow_ups() -> dict:
    """Record sends and replies, and write the follow-ups that are nearly due, for every workspace."""
    lock = _lock()
    if lock is not None and not lock.acquire():
        log.info("follow-up check skipped: another one is still running")
        return {"skipped": True}
    try:
        summary = asdict(asyncio.run(jobs.check_follow_ups()))
        log.info("follow-up check: %s", summary)
        return summary
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001 - it expired already
                pass
