from django.db import migrations, models
import django.db.models.deletion

SYSTEM_GROUP_FLAGGED = "flagged"
LEGACY_FLAGGED_CHAT = "__flagged_chat__"


def group_chat_workspace_name(group_name: str) -> str:
    return f"__group_chat__{group_name}"


def bootstrap_groups(apps, schema_editor):
    Workspace = apps.get_model("nodepoint", "Workspace")
    WorkspaceGroup = apps.get_model("nodepoint", "WorkspaceGroup")
    WorkspaceGroupMembership = apps.get_model("nodepoint", "WorkspaceGroupMembership")
    Conversation = apps.get_model("nodepoint", "Conversation")

    flagged_group, _ = WorkspaceGroup.objects.get_or_create(name=SYSTEM_GROUP_FLAGGED)

    internal_prefix = "__group_chat__"
    for ws in Workspace.objects.filter(is_flag=True):
        if ws.name in (LEGACY_FLAGGED_CHAT, SYSTEM_GROUP_FLAGGED):
            continue
        if ws.name.startswith(internal_prefix):
            continue
        WorkspaceGroupMembership.objects.get_or_create(
            group=flagged_group,
            workspace=ws,
        )

    new_chat_name = group_chat_workspace_name(SYSTEM_GROUP_FLAGGED)
    new_chat_ws, _ = Workspace.objects.get_or_create(
        name=new_chat_name,
        defaults={"is_flag": False},
    )

    try:
        legacy_ws = Workspace.objects.get(name=LEGACY_FLAGGED_CHAT)
    except Workspace.DoesNotExist:
        return

    for conv in Conversation.objects.filter(workspace=legacy_ws):
        conv.workspace = new_chat_ws
        conv.save(update_fields=["workspace"])


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0007_documentchunk_chunk_fks"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceGroup",
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
                ("name", models.CharField(max_length=255, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="WorkspaceGroupMembership",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="nodepoint.workspacegroup",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="group_memberships",
                        to="nodepoint.workspace",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="workspacegroupmembership",
            constraint=models.UniqueConstraint(
                fields=("group", "workspace"),
                name="unique_workspace_per_group",
            ),
        ),
        migrations.RunPython(bootstrap_groups, migrations.RunPython.noop),
    ]
