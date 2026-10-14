"""Celery tasks."""
from apps.worker.tasks import external_reliability  # noqa: F401
from apps.worker.tasks import prediction_snapshots  # noqa: F401
from apps.worker.tasks import prediction_v2  # noqa: F401
