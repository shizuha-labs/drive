# Generated for DRV-36 activity/audit schema.

from django.db import migrations, models
import django.utils.timezone


def backfill_activity_canonical_fields(apps, schema_editor):
    Activity = apps.get_model('drive', 'Activity')
    db_alias = schema_editor.connection.alias
    for activity in Activity.objects.using(db_alias).select_related('file', 'folder').iterator():
        update = {
            'actor_id': activity.actor_id or str(activity.user_id),
            'event_type': activity.event_type or activity.action,
            'metadata': activity.metadata or activity.details or {},
            'timestamp': activity.timestamp or activity.created_at or django.utils.timezone.now(),
        }
        if activity.file_id:
            update['resource_id'] = activity.resource_id or activity.file_id
            update['resource_type'] = activity.resource_type or 'file'
            if activity.file:
                update['resource_name'] = activity.resource_name or activity.file.name
                update['parent_id'] = activity.parent_id or activity.file.folder_id
        elif activity.folder_id:
            update['resource_id'] = activity.resource_id or activity.folder_id
            update['resource_type'] = activity.resource_type or 'folder'
            if activity.folder:
                update['resource_name'] = activity.resource_name or activity.folder.name
                update['parent_id'] = activity.parent_id or activity.folder.parent_id
        Activity.objects.using(db_alias).filter(pk=activity.pk).update(**update)


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0010_merge_drv30_drv42'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='activity',
            options={'ordering': ['-timestamp'], 'verbose_name_plural': 'Activities'},
        ),
        migrations.AddField(
            model_name='activity',
            name='actor_id',
            field=models.CharField(blank=True, db_index=True, help_text='Canonical actor/user ID (DRV-36)', max_length=64),
        ),
        migrations.AddField(
            model_name='activity',
            name='event_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('create', 'Create'),
                    ('upload', 'Upload'),
                    ('update', 'Update'),
                    ('rename', 'Rename'),
                    ('move', 'Move'),
                    ('copy', 'Copy'),
                    ('delete', 'Delete'),
                    ('share', 'Share'),
                    ('unshare', 'Unshare'),
                    ('trash', 'Trash'),
                    ('download', 'Download'),
                    ('restore', 'Restore'),
                    ('view', 'View'),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='activity',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='activity',
            name='parent_id',
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='activity',
            name='resource_id',
            field=models.UUIDField(blank=True, db_index=True, help_text='Canonical file/folder UUID (DRV-36)', null=True),
        ),
        migrations.AddField(
            model_name='activity',
            name='resource_name',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='activity',
            name='resource_type',
            field=models.CharField(blank=True, choices=[('file', 'File'), ('folder', 'Folder')], db_index=True, max_length=16),
        ),
        migrations.AddField(
            model_name='activity',
            name='timestamp',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_activity_canonical_fields, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='activity',
            name='timestamp',
            field=models.DateTimeField(db_index=True, default=django.utils.timezone.now),
        ),
        migrations.AddIndex(
            model_name='activity',
            index=models.Index(fields=['resource_id', '-timestamp'], name='drive_activ_resourc_c31520_idx'),
        ),
        migrations.AddIndex(
            model_name='activity',
            index=models.Index(fields=['actor_id', '-timestamp'], name='drive_activ_actor_i_816051_idx'),
        ),
    ]
