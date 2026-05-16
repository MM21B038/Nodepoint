from django.db import migrations


def ensure_global(apps, schema_editor):
    Workspace = apps.get_model("nodepoint", "Workspace")
    Workspace.objects.get_or_create(
        name="global",
        defaults={"is_flag": True},
    )
    for ws in Workspace.objects.filter(name="global"):
        if not ws.is_flag:
            ws.is_flag = True
            ws.save(update_fields=["is_flag"])


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0003_chatbranch_is_internal"),
    ]

    operations = [
        migrations.RunPython(ensure_global, migrations.RunPython.noop),
    ]
