from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "telegram_ai_saas",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    beat_schedule={
        "dispatch-due-broadcasts": {
            "task": "broadcasts.dispatch_due",
            "schedule": 30.0,
        },
        "recover-stale-broadcasts": {
            "task": "broadcasts.recover_stale",
            "schedule": 300.0,
        },
        "subscription-notification-scan": {
            "task": "notifications.subscription_scan",
            "schedule": 60.0,
        },
        "recover-stale-notifications": {
            "task": "notifications.recover_stale",
            "schedule": 900.0,
        },
        "reconcile-pending-payments": {
            "task": "payments.reconcile_pending",
            "schedule": 30.0,
        },
    },
)
