import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0015_auth_user_ownership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="allowed_scopes",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="deletion_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="deletion_requested_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deletions_requested",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="purge_scheduled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("pending_deletion", "Pending deletion"),
                    ("purged", "Purged"),
                ],
                default="active",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="ApiUsageLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("method", models.CharField(max_length=10)),
                ("path", models.CharField(max_length=500)),
                ("url_name", models.CharField(blank=True, default="", max_length=100)),
                ("scope", models.CharField(blank=True, default="", max_length=64)),
                ("status_code", models.PositiveSmallIntegerField()),
                ("duration_ms", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "api_key",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="usage_logs",
                        to="nodepoint.apikey",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_usage_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "-created_at"], name="apiusage_user_created"
                    ),
                    models.Index(
                        fields=["url_name", "-created_at"], name="apiusage_url_created"
                    ),
                ],
            },
        ),
    ]
