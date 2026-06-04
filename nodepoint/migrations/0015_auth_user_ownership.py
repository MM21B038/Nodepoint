import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def bootstrap_owners(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    UserProfile = apps.get_model("nodepoint", "UserProfile")
    Workspace = apps.get_model("nodepoint", "Workspace")
    WorkspaceGroup = apps.get_model("nodepoint", "WorkspaceGroup")

    system, created = User.objects.get_or_create(
        username="system",
        defaults={"is_staff": True, "is_superuser": True, "is_active": True},
    )
    if created:
        system.password = "!"
        system.save(update_fields=["password"])

    UserProfile.objects.get_or_create(
        user=system,
        defaults={"role": "superadmin"},
    )

    Workspace.objects.filter(owner__isnull=True).update(owner=system)
    WorkspaceGroup.objects.filter(owner__isnull=True).update(owner=system)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("nodepoint", "0014_conversation_multi_session"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("superadmin", "Superadmin"),
                            ("admin", "Admin"),
                            ("user", "User"),
                        ],
                        default="user",
                        max_length=20,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_accounts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "managed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="managed_users",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ApiKey",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                ("prefix", models.CharField(db_index=True, max_length=16)),
                ("key_hash", models.CharField(max_length=64)),
                ("allowed_scopes", models.JSONField(default=list)),
                ("expires_at", models.DateTimeField()),
                ("is_active", models.BooleanField(default=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="api_keys_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_keys",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="apikey",
            index=models.Index(fields=["prefix", "is_active"], name="apikey_prefix_active"),
        ),
        migrations.AddField(
            model_name="workspace",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workspaces",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="workspacegroup",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workspace_groups",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(bootstrap_owners, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="workspace",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workspaces",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="workspacegroup",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workspace_groups",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
