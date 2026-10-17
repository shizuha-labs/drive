from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0017_ven151_seed_tiers'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScheduledMechanismRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mechanism', models.CharField(max_length=100, unique=True)),
                ('last_started_at', models.DateTimeField(blank=True, null=True)),
                ('last_succeeded_at', models.DateTimeField(blank=True, null=True)),
                ('last_failed_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('consecutive_failures', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
