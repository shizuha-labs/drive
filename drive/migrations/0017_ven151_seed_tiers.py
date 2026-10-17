# Data migration: seed default storage tiers for VEN-151

from django.db import migrations


def seed_tiers(apps, schema_editor):
    StorageTier = apps.get_model('drive', 'StorageTier')
    tiers = [
        {
            'name': 'Free',
            'slug': 'free',
            'quota_bytes': 5 * 1024 * 1024 * 1024,  # 5 GB
            'price_monthly': 0,
            'price_yearly': 0,
            'description': 'Free tier with 5GB storage. Perfect for getting started.',
            'sort_order': 0,
        },
        {
            'name': 'Pro',
            'slug': 'pro',
            'quota_bytes': 50 * 1024 * 1024 * 1024,  # 50 GB
            'price_monthly': 299,
            'price_yearly': 0,
            'description': 'Pro tier with 50GB storage. For power users.',
            'sort_order': 1,
        },
        {
            'name': 'Team',
            'slug': 'team',
            'quota_bytes': 200 * 1024 * 1024 * 1024,  # 200 GB
            'price_monthly': 799,
            'price_yearly': 0,
            'description': 'Team tier with 200GB storage. For teams and organizations.',
            'sort_order': 2,
        },
    ]
    for tier_data in tiers:
        StorageTier.objects.get_or_create(
            slug=tier_data['slug'],
            defaults=tier_data,
        )


def reverse_tiers(apps, schema_editor):
    StorageTier = apps.get_model('drive', 'StorageTier')
    StorageTier.objects.filter(slug__in=['free', 'pro', 'team']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0016_ven151_storage_tiers'),
    ]

    operations = [
        migrations.RunPython(seed_tiers, reverse_tiers),
    ]
