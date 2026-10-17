# Generated manually for VEN-151 storage tier enforcement

import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0015_drv59_file_activity'),
    ]

    operations = [
        migrations.CreateModel(
            name='StorageTier',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text='Tier name (free, pro, team)', max_length=50, unique=True)),
                ('slug', models.SlugField(help_text='URL-friendly identifier', max_length=50, unique=True)),
                ('quota_bytes', models.BigIntegerField(help_text='Storage quota in bytes')),
                ('price_monthly', models.DecimalField(decimal_places=2, default=0, help_text='Monthly price in INR', max_digits=10)),
                ('price_yearly', models.DecimalField(decimal_places=2, default=0, help_text='Yearly price in INR', max_digits=10)),
                ('description', models.TextField(blank=True, help_text='Tier description')),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0, help_text='Display order')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['sort_order'],
            },
        ),
        migrations.CreateModel(
            name='UserTier',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('owner_id', models.CharField(help_text='User ID from Shizuha ID', max_length=64, unique=True)),
                ('quota_bytes', models.BigIntegerField(help_text='Effective quota in bytes (from tier or custom)')),
                ('pending_quota_bytes', models.BigIntegerField(blank=True, help_text='Pending quota for next billing cycle', null=True)),
                ('billing_cycle', models.CharField(choices=[('monthly', 'Monthly'), ('yearly', 'Yearly')], default='monthly', max_length=10)),
                ('next_billing_date', models.DateTimeField(blank=True, null=True)),
                ('pending_entitlement_reference', models.CharField(blank=True, help_text='Trusted billing entitlement authorizing a paid pending tier', max_length=255, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pending_tier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pending_users', to='drive.StorageTier')),
                ('tier', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='users', to='drive.StorageTier')),
            ],
            options={
                'verbose_name_plural': 'User tiers',
            },
        ),
        migrations.AddIndex(
            model_name='usertier',
            index=models.Index(fields=['owner_id'], name='drive_usert_owner_i_idx'),
        ),
        migrations.AddIndex(
            model_name='usertier',
            index=models.Index(fields=['tier'], name='drive_usert_tier_id_idx'),
        ),
        migrations.CreateModel(
            name='StorageQuotaReservation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('owner_id', models.CharField(max_length=64)),
                ('size_bytes', models.BigIntegerField()),
                ('status', models.CharField(choices=[('active', 'Active'), ('committed', 'Committed'), ('released', 'Released')], default='active', max_length=16)),
                ('expires_at', models.DateTimeField()),
                ('committed_at', models.DateTimeField(blank=True, null=True)),
                ('released_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name='storagequotareservation',
            index=models.Index(fields=['owner_id', 'status', 'expires_at'], name='drive_quota_res_active_idx'),
        ),
    ]
