# Generated manually for ServiceStorageUsage model

import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0002_alter_activity_user_id_alter_file_owner_id_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceStorageUsage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('owner_id', models.CharField(help_text='User ID from Shizuha ID', max_length=64)),
                ('service', models.CharField(help_text='Service name (e.g. wiki, notes, finance)', max_length=50)),
                ('used_bytes', models.BigIntegerField(default=0)),
                ('file_count', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name_plural': 'Service storage usage',
                'unique_together': {('owner_id', 'service')},
            },
        ),
        migrations.AddIndex(
            model_name='servicestorageusage',
            index=models.Index(fields=['owner_id'], name='drive_servi_owner_i_idx'),
        ),
    ]
