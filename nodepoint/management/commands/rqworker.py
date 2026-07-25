from typing import Tuple
from django_rq.management.commands.rqworker import Command as RQWorkerCommand

from nodepoint.rq_hooks import install_rq_worker_hooks
from nodepoint.services.preprocess_recovery import (
    has_orphaned_preprocess_work,
    maybe_run_startup_recovery,
)


def should_run_startup_recovery(queues: Tuple[str, ...]) -> bool:
    if "orchestrator" in queues:
        return True
    if "chunk" in queues and has_orphaned_preprocess_work():
        return True
    return False


class Command(RQWorkerCommand):
    """RQ worker with orchestrator startup preprocess recovery."""

    def handle(self, *args, **options):
        import logging

        install_rq_worker_hooks()
        logger = logging.getLogger(__name__)
        if should_run_startup_recovery(args):
            logger.info("rqworker: running startup preprocess recovery (queues=%s)", args)
            maybe_run_startup_recovery()
        else:
            logger.info("rqworker: skipping startup preprocess recovery (queues=%s)", args)
        super().handle(*args, **options)
