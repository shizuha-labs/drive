from django.db import migrations, models


class Migration(migrations.Migration):
    """DRV-14: add allow_cross_org flag to Share (INV-4 — explicit cross-org gate)."""

    dependencies = [
        ('drive', '0005_share_revoked_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='share',
            name='allow_cross_org',
            field=models.BooleanField(
                default=False,
                help_text='Explicitly permits a cross-org share (INV-4)',
            ),
        ),
    ]
