# Generated for DRV-10 resumable uploads.
import uuid
import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone
from datetime import timedelta


def default_expiry():
    return timezone.now() + timedelta(days=1)


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0003_servicestorageusage'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResumableUpload',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('owner_id', models.CharField(db_index=True, help_text='User ID from Shizuha ID', max_length=64)),
                ('name', models.CharField(max_length=255)),
                ('size', models.BigIntegerField(help_text='Expected final size in bytes')),
                ('mime_type', models.CharField(blank=True, max_length=100)),
                ('chunk_size', models.PositiveIntegerField(default=5242880)),
                ('uploaded_bytes', models.BigIntegerField(default=0)),
                ('checksum_sha256', models.CharField(blank=True, max_length=64)),
                ('temp_path', models.CharField(max_length=500)),
                ('status', models.CharField(choices=[('active', 'Active'), ('completed', 'Completed'), ('aborted', 'Aborted')], db_index=True, default='active', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('expires_at', models.DateTimeField(default=default_expiry, db_index=True)),
                ('file', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='upload_sessions', to='drive.file')),
                ('folder', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resumable_uploads', to='drive.folder')),
            ],
            options={
                'ordering': ['-updated_at'],
                'indexes': [models.Index(fields=['owner_id', 'status'], name='drive_resum_owner_i_8a1915_idx'), models.Index(fields=['expires_at'], name='drive_resum_expires_4ce5f7_idx')],
            },
        ),
    ]
