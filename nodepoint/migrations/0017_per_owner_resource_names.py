import os
import shutil

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _group_chat_name(owner_id: int, group_name: str) -> str:
    return f"__group_chat__{owner_id}__{group_name}"


def migrate_media_and_chat_workspaces(apps, schema_editor):
    Workspace = apps.get_model("nodepoint", "Workspace")
    WorkspaceGroup = apps.get_model("nodepoint", "WorkspaceGroup")
    media_root = getattr(settings, "MEDIA_ROOT", "") or ""

    for ws in Workspace.objects.select_related().iterator():
        if not ws.owner_id:
            continue
        new_chat_name = None
        if ws.name.startswith("__group_chat__") and "__" not in ws.name[len("__group_chat__") :]:
            suffix = ws.name.removeprefix("__group_chat__")
            group = (
                WorkspaceGroup.objects.filter(name=suffix, owner_id=ws.owner_id).first()
                or WorkspaceGroup.objects.filter(name=suffix).first()
            )
            if group:
                new_chat_name = _group_chat_name(group.owner_id, group.name)
                if new_chat_name != ws.name:
                    Workspace.objects.filter(pk=ws.pk).update(name=new_chat_name)
                    ws.name = new_chat_name

        if not media_root:
            continue
        old_path = os.path.join(media_root, "workspaces", ws.name)
        new_path = os.path.join(media_root, "workspaces", str(ws.owner_id), ws.name)
        if os.path.isdir(old_path) and not os.path.exists(new_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(old_path, new_path)


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0016_account_lifecycle_api_usage"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workspace",
            name="name",
            field=models.CharField(max_length=255),
        ),
        migrations.AlterField(
            model_name="workspacegroup",
            name="name",
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name="workspace",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_workspace_name_per_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="workspacegroup",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_group_name_per_owner",
            ),
        ),
        migrations.RunPython(migrate_media_and_chat_workspaces, migrations.RunPython.noop),
    ]
