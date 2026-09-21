import time
from datetime import timedelta

import structlog
from sqlalchemy import or_, select

from sourcelens.jobs.celery_app import app
from sourcelens.persistence.database import session
from sourcelens.persistence.models import Job, Outbox, now


def dispatch_once() -> int:
    sent = 0
    with session() as db, db.begin():
        rows = db.scalars(
            select(Outbox)
            .join(Job)
            .where(
                Job.status.in_(["queued", "running"]),
                or_(
                    Outbox.published_at.is_(None),
                    Outbox.published_at < now() - timedelta(seconds=60),
                ),
                or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now()),
            )
            .with_for_update(of=Outbox, skip_locked=True)
            .limit(20)
        )
        for row in rows:
            app.send_task("sourcelens.ingest", args=[str(row.job_id)], queue="ingestion")
            row.published_at = now()
            sent += 1
    return sent


def main() -> None:
    while True:
        try:
            dispatch_once()
        except Exception:
            structlog.get_logger().error("dispatch_unavailable")
        time.sleep(2)


if __name__ == "__main__":
    main()
