import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0006_share_allow_cross_org'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShareAccessLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('share', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='access_logs',
                    to='drive.share',
                )),
                ('share_id_copy', models.UUIDField(db_index=True)),
                ('accessed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('viewer_id', models.CharField(blank=True, max_length=64, null=True)),
                ('viewer_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True)),
                ('action', models.CharField(choices=[
                    ('view', 'View'),
                    ('download', 'Download'),
                    ('password_fail', 'Password Fail'),
                    ('denied', 'Denied'),
                ], max_length=20)),
                ('result', models.CharField(choices=[
                    ('ok', 'OK'),
                    ('denied', 'Denied'),
                    ('expired', 'Expired'),
                    ('bad_password', 'Bad Password'),
                ], max_length=20)),
            ],
            options={
                'verbose_name': 'Share Access Log',
                'verbose_name_plural': 'Share Access Logs',
                'ordering': ['-accessed_at'],
                'indexes': [
                    models.Index(fields=['share', '-accessed_at'], name='drive_share_share_i_accesse_idx'),
                ],
            },
        ),
    ]
