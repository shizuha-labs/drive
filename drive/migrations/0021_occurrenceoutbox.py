from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0020_conflictartifact_change'),
    ]

    operations = [
        migrations.CreateModel(
            name='OccurrenceOutbox',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source', models.CharField(max_length=255)),
                ('event_id', models.CharField(max_length=255)),
                ('event_type', models.CharField(max_length=255)),
                ('occurred_at', models.DateTimeField()),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                ('subject', models.CharField(max_length=255)),
                ('actor_user_id', models.CharField(blank=True, max_length=255)),
                ('organization_id', models.CharField(blank=True, max_length=255)),
                ('resource_type', models.CharField(max_length=64)),
                ('resource_id', models.CharField(max_length=255)),
                ('data', models.JSONField(default=dict)),
                ('data_sha256', models.CharField(max_length=64)),
                ('state', models.CharField(choices=[('pending', 'Pending'), ('delivered', 'Delivered'), ('failed', 'Failed')], db_index=True, default='pending', max_length=16)),
                ('delivery_attempts', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('source', 'event_id'), name='drive_occurrence_outbox_source_event_uniq')],
                'indexes': [models.Index(fields=['state', 'recorded_at'], name='drive_outbox_state_age_idx')],
            },
        ),
    ]
