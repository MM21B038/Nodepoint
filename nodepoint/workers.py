from rq.job import Job
from rq.queue import Queue
from rq.worker import Worker

from django.db import close_old_connections

from django_rq.utils import reset_db_connections


class NodepointWorker(Worker):
    """RQ worker that closes fork-unsafe connections before each job fork."""

    def fork_work_horse(self, job: Job, queue: Queue):
        reset_db_connections()
        super().fork_work_horse(job, queue)

    def perform_job(self, job: Job, queue: Queue) -> bool:
        try:
            return super().perform_job(job, queue)
        finally:
            close_old_connections()
