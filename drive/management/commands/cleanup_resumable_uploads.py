"""Delete expired resumable-upload staging files (DRV-33)."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from drive.models import ResumableUpload
from drive.resumable_storage import delete_staging, staging_exists


class Command(BaseCommand):
    help = "Mark expired active resumable upload sessions aborted and delete their temp files."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report work without mutating sessions or storage.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()
        expired = ResumableUpload.objects.filter(
            status__in=[ResumableUpload.Status.ACTIVE, ResumableUpload.Status.ABORTED],
            expires_at__lte=now,
        )
        count = expired.count()
        deleted_files = 0
        for session in expired.iterator():
            if staging_exists(session.temp_path):
                if not dry_run:
                    try:
                        deleted_files += delete_staging(session.temp_path)
                    except OSError as exc:
                        self.stderr.write(f"failed to delete {session.temp_path}: {exc}")
                else:
                    deleted_files += 1
            if not dry_run:
                session.status = ResumableUpload.Status.ABORTED
                session.save(update_fields=["status", "updated_at"])
        verb = "Would abort" if dry_run else "Aborted"
        self.stdout.write(f"{verb} {count} expired resumable upload session(s); temp files deleted: {deleted_files}")
