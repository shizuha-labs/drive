# Generated for DRV-42 trash restore windows.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0008_drv22_waitlist'),
    ]

    operations = [
        migrations.AddField(
            model_name='file',
            name='deleted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='file',
            name='deleted_by',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='file',
            name='restore_until',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
