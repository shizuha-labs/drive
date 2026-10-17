from django.db import migrations


class Migration(migrations.Migration):
    """Rejoin the VEN-151 and VEN-153 migration branches."""

    dependencies = [
        ("drive", "0018_merge_ven153_digital_accounting"),
        ("drive", "0018_scheduledmechanismrun"),
    ]

    operations = []
