from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodepoint", "0002_conversation_chatbranch_chatmessage_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbranch",
            name="is_internal",
            field=models.BooleanField(
                default=False,
                help_text="Compression handoff branch; hidden from user-facing APIs.",
            ),
        ),
    ]
