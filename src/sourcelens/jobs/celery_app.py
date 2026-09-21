from celery import Celery

from sourcelens.config import get_settings

app = Celery("sourcelens", broker=get_settings().redis_url)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=get_settings().job_timeout + 30,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": get_settings().job_timeout + 60},
)
