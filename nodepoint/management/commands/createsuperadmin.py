from django.core.management.base import BaseCommand, CommandError

from nodepoint.auth.users import User, create_account
from nodepoint.enums import UserRole


class Command(BaseCommand):
    help = "Create a superadmin account (bootstrap only)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--noinput", action="store_true", help="Use password from env")

    def handle(self, *args, **options):
        username = options["username"]
        if User.objects.filter(username=username).exists():
            raise CommandError(f"User '{username}' already exists")

        if options["noinput"]:
            import os

            password = os.environ.get("SUPERADMIN_PASSWORD")
            if not password:
                raise CommandError("Set SUPERADMIN_PASSWORD when using --noinput")
        else:
            password = self.get_pass("Password: ")
            confirm = self.get_pass("Password (again): ")
            if password != confirm:
                raise CommandError("Passwords do not match")

        user = create_account(
            username=username,
            password=password,
            role=UserRole.SUPERADMIN,
        )
        user.is_staff = True
        user.is_superuser = True
        if options["email"]:
            user.email = options["email"]
        user.save(update_fields=["is_staff", "is_superuser", "email"])
        self.stdout.write(self.style.SUCCESS(f"Superadmin '{username}' created (id={user.pk})"))

    def get_pass(self, prompt: str) -> str:
        import getpass

        return getpass.getpass(prompt)
