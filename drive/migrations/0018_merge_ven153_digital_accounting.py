from django.db import migrations


class Migration(migrations.Migration):
    """Merge the two 0016 leaves that formed when VEN-153 (drive#18) landed.

    ``0016_share_digital_accounting_root_with_org`` and
    ``0016_ven153_deletion_request`` both declare ``0015_drv59_file_activity``
    as their parent, leaving the graph with two leaves and failing
    ``makemigrations --check``. This merge carries no operations; it only
    rejoins the branches so the graph has a single leaf again.
    """

    dependencies = [
        ("drive", "0016_share_digital_accounting_root_with_org"),
        ("drive", "0017_deletionprocessorstate_and_pending_constraint"),
    ]

    operations = []
