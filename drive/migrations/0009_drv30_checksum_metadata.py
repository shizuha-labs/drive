# Generated for DRV-30 upload integrity canonical checksum metadata.
from django.db import migrations, models


def copy_legacy_sha256(apps, schema_editor):
    File = apps.get_model('drive', 'File')
    File.objects.filter(checksum='', checksum_sha256__gt='').update(
        checksum=models.F('checksum_sha256'),
        checksum_algorithm='sha256',
    )


def reverse_copy_legacy_sha256(apps, schema_editor):
    File = apps.get_model('drive', 'File')
    File.objects.filter(checksum_sha256='', checksum_algorithm='sha256', checksum__gt='').update(
        checksum_sha256=models.F('checksum'),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0008_drv22_waitlist'),
    ]

    operations = [
        migrations.AddField(
            model_name='file',
            name='checksum',
            field=models.CharField(blank=True, default='', max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='file',
            name='checksum_algorithm',
            field=models.CharField(default='sha256', max_length=16),
        ),
        migrations.RunPython(copy_legacy_sha256, reverse_copy_legacy_sha256),
    ]
