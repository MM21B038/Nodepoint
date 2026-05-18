import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0006_remove_global_workspace"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentChunk",
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
                ("index", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("QUEUED", "Queued"),
                            ("INPROGRESS", "In Progress"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                            ("TERMINATED", "Terminated"),
                            ("INVALID", "Invalid"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                (
                    "vector",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("QUEUED", "Queued"),
                            ("INPROGRESS", "In Progress"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                            ("TERMINATED", "Terminated"),
                            ("INVALID", "Invalid"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="nodepoint.document",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("document", "index"),
                        name="unique_chunk_index_per_document",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="knowledgeentity",
            name="chunk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="entities",
                to="nodepoint.documentchunk",
            ),
        ),
        migrations.AddField(
            model_name="knowledgerelation",
            name="chunk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="relations",
                to="nodepoint.documentchunk",
            ),
        ),
    ]
