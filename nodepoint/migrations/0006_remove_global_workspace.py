from django.db import migrations


def remove_global_workspace(apps, schema_editor):
    Workspace = apps.get_model("nodepoint", "Workspace")
    Workspace.objects.filter(name="global").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0005_conversation_unique_per_workspace"),
    ]

    operations = [
        migrations.RunPython(remove_global_workspace, migrations.RunPython.noop),
    ]
