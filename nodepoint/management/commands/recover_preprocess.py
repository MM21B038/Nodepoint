from django.core.management.base import BaseCommand

from nodepoint.services.preprocess_recovery import (
    maybe_run_startup_recovery,
    run_preprocess_recovery,
)


class Command(BaseCommand):
    help = (
        "Reset orphaned QUEUED/INPROGRESS chunks and re-enqueue global "
        "chunk/vector preprocess work (use after Redis/worker restart)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip the startup Redis lock and run recovery immediately.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        if force:
            stats = run_preprocess_recovery()
            self.stdout.write(self.style.SUCCESS(f"Recovery completed: {stats}"))
            return

        stats = maybe_run_startup_recovery()
        if stats is None:
            self.stdout.write("Recovery skipped (disabled, lock held, or already ran).")
            return
        self.stdout.write(self.style.SUCCESS(f"Recovery completed: {stats}"))
