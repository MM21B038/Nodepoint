from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0008_workspace_groups"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="workspace",
            name="is_flag",
        ),
    ]
