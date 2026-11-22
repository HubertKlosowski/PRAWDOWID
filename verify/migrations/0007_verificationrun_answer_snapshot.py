from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("verify", "0006_request_limits_and_security_events")]

    operations = [
        migrations.AddField(
            model_name="verificationrun",
            name="answer_snapshot",
            field=models.JSONField(null=True),
        ),
    ]
