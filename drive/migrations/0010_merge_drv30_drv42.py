# Generated to merge DRV-30 checksum metadata and DRV-42 trash restore window migrations.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0009_drv30_checksum_metadata'),
        ('drive', '0009_file_trash_restore_window'),
    ]

    operations = []
