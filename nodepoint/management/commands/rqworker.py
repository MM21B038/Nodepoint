from django_rq.management.commands.rqworker import Command as RQWorkerCommand

from nodepoint.services.preprocess_recovery import maybe_run_startup_recovery


def should_run_startup_recovery(queues: tuple[str, ...]) -> bool:
    return "orchestrator" in queues


class Command(RQWorkerCommand):
    """RQ worker with orchestrator startup preprocess recovery."""

    def handle(self, *args, **options):
        if should_run_startup_recovery(args):
            maybe_run_startup_recovery()
        super().handle(*args, **options)
