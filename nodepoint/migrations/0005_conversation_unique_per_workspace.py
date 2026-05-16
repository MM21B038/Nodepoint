from django.db import migrations, models


def dedupe_conversations_per_workspace(apps, schema_editor):
    Conversation = apps.get_model("nodepoint", "Conversation")
    seen_workspaces: set[int] = set()
    for conv in Conversation.objects.order_by("workspace_id", "-updated_at"):
        if conv.workspace_id in seen_workspaces:
            conv.delete()
        else:
            seen_workspaces.add(conv.workspace_id)


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0004_ensure_global_workspace"),
    ]

    operations = [
        migrations.RunPython(dedupe_conversations_per_workspace, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.UniqueConstraint(
                fields=("workspace",),
                name="unique_conversation_per_workspace",
            ),
        ),
    ]
