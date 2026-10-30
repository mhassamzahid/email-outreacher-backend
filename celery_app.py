"""Celery: background work runs in worker processes, and Celery beat schedules it. Redis is the broker.

Run them next to the API (see README):
    celery -A celery_app worker --loglevel=info      (on Windows add --pool=solo)
    celery -A celery_app beat --loglevel=info        (exactly one beat, or checks are scheduled twice)

Beat starts the follow-up check every FOLLOWUP_CHECK_SECONDS (30 minutes by default).
"""

from pathlib import Path

from celery import Celery

from config import get_settings

settings = get_settings()

app = Celery("coldbrew", broker=settings.broker_url, include=["tasks"])
app.conf.update(
    timezone="UTC",
    task_ignore_result=True,  # results are logged, not stored
    task_acks_late=True,  # a check interrupted by a restart runs again
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "follow-ups": {
            "task": "coldbrew.check_follow_ups",
            "schedule": float(settings.followup_check_seconds),
            # A check still queued when the next one is due is dropped, so a backlog never piles up.
            "options": {"expires": float(settings.followup_check_seconds)},
        }
    },
)

if settings.broker_url.startswith("filesystem://"):
    # Local development without Redis (CELERY_BROKER_URL=filesystem://): a folder stands in for the
    # broker, so a separate worker and beat can still talk. Never for production.
    folder = Path(__file__).with_name(".celery-broker")
    folder.mkdir(exist_ok=True)
    app.conf.broker_transport_options = {
        "data_folder_in": str(folder), "data_folder_out": str(folder), "control_folder": str(folder / "control"),
        "store_processed": False,
    }  # fmt: skip
