from django.db import migrations


DIGITAL_ACCOUNTING_ROOT_ID = "203fa7ec-8bf7-4eed-9855-fe65b83068c9"
DIGITAL_ACCOUNTING_SHARE_ID = "23b06219-78e0-5408-9a47-bedca3343e81"
DIGITAL_ORGANIZATION_ID = "2"


def share_digital_accounting_root(apps, schema_editor):
    Folder = apps.get_model("drive", "Folder")
    Share = apps.get_model("drive", "Share")

    folder = Folder.objects.filter(
        id=DIGITAL_ACCOUNTING_ROOT_ID,
        is_trashed=False,
    ).first()
    if folder is None:
        return

    # Preserve any valid operator-created organization grant. Otherwise create
    # one deterministic, exact-org edit share whose permission inheritance
    # covers every financial-year child folder.
    if Share.objects.filter(
        folder=folder,
        share_type="org",
        organization_id=DIGITAL_ORGANIZATION_ID,
        permission="edit",
        is_active=True,
        revoked_at__isnull=True,
    ).exists():
        return

    Share.objects.create(
        id=DIGITAL_ACCOUNTING_SHARE_ID,
        folder=folder,
        share_type="org",
        organization_id=DIGITAL_ORGANIZATION_ID,
        permission="edit",
        created_by_id=folder.owner_id,
        allow_download=True,
        allow_cross_org=False,
        is_active=True,
    )


def unshare_digital_accounting_root(apps, schema_editor):
    Share = apps.get_model("drive", "Share")
    Share.objects.filter(id=DIGITAL_ACCOUNTING_SHARE_ID).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("drive", "0015_drv59_file_activity"),
    ]

    operations = [
        migrations.RunPython(
            share_digital_accounting_root,
            unshare_digital_accounting_root,
        ),
    ]
