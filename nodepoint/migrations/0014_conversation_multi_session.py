from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0013_typed_group_tags"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="conversation",
            name="unique_conversation_per_workspace",
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["workspace", "-updated_at"],
                name="conversation_workspace_updated",
            ),
        ),
    ]
