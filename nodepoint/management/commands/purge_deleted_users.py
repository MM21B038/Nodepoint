from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from nodepoint.auth.account_lifecycle import purge_due_accounts
from nodepoint.models import ApiUsageLog


class Command(BaseCommand):
    help = "Purge accounts past deletion grace period and trim old API usage logs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List accounts that would be purged without deleting",
        )

    def handle(self, *args, **options):
        from nodepoint.models import UserProfile
        from nodepoint.enums import AccountStatus

        due = UserProfile.objects.filter(
            status=AccountStatus.PENDING_DELETION,
            purge_scheduled_at__lte=timezone.now(),
        ).select_related("user")
        if options["dry_run"]:
            for profile in due:
                self.stdout.write(
                    f"Would purge user id={profile.user_id} "
                    f"username={profile.user.username} "
                    f"purge_at={profile.purge_scheduled_at}"
                )
            self.stdout.write(self.style.WARNING(f"Due count: {due.count()}"))
            return

        deleted = purge_due_accounts()
        retention = getattr(settings, "API_USAGE_LOG_RETENTION_DAYS", 90)
        cutoff = timezone.now() - timedelta(days=retention)
        trimmed, _ = ApiUsageLog.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {len(deleted)} user(s); removed {trimmed} usage log row(s)."
            )
        )
