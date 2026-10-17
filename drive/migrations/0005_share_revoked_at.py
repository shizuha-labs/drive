from django.db import migrations, models


class Migration(migrations.Migration):
    """DRV-13 M-4: add Share.revoked_at for soft-revoke (additive, nullable)."""

    dependencies = [
        ('drive', '0004_resumableupload'),
    ]

    operations = [
        migrations.AddField(
            model_name='share',
            name='revoked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
