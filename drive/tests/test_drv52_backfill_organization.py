"""DRV-52: content/search/activity organization backfill tests."""
import tempfile

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from drive.models import Activity, File, FileType, Folder


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv52-'))
class DriveOrganizationBackfillCommandTests(TestCase):
    def _file(
        self,
        *,
        name='legacy.txt',
        content=b'legacy searchable text',
        mime_type='text/plain',
        file_type=FileType.DOCUMENT,
    ):
        file_obj = File(
            name=name,
            owner_id='user-1',
            file_type=file_type,
            mime_type=mime_type,
            size=len(content),
            extension=name.rsplit('.', 1)[-1].lower() if '.' in name else '',
            is_processed=False,
        )
        file_obj.file.save(name, ContentFile(content), save=False)
        file_obj.save()
        return file_obj

    def test_content_backfill_populates_text_without_touching_updated_at(self):
        file_obj = self._file(content=b'alpha beta old content')
        old_updated_at = timezone.now() - timezone.timedelta(days=30)
        File.objects.filter(pk=file_obj.pk).update(updated_at=old_updated_at)

        call_command('backfill_drive_organization', '--only', 'content')

        file_obj.refresh_from_db()
        self.assertEqual(file_obj.content_text, 'alpha beta old content')
        self.assertTrue(file_obj.is_processed)
        self.assertEqual(file_obj.processing_error, '')
        self.assertEqual(file_obj.updated_at, old_updated_at)

    def test_content_backfill_sets_empty_sentinel_and_is_idempotent(self):
        file_obj = self._file(
            name='legacy.zip',
            content=b'PK\x03\x04binary',
            mime_type='application/zip',
            file_type=FileType.ARCHIVE,
        )

        call_command('backfill_drive_organization', '--only', 'content')
        file_obj.refresh_from_db()
        self.assertEqual(file_obj.content_text, '')
        self.assertTrue(file_obj.is_processed)

        # Empty string is the attempted-empty sentinel; a second run skips it.
        out = tempfile.TemporaryFile(mode='w+')
        call_command('backfill_drive_organization', '--only', 'content', stdout=out)
        out.seek(0)
        self.assertIn('content_seen=0', out.read())

    def test_content_backfill_skips_document_slow_path_unless_requested(self):
        file_obj = self._file(
            name='legacy.pdf',
            content=b'%PDF-pretend',
            mime_type='application/pdf',
            file_type=FileType.DOCUMENT,
        )

        call_command('backfill_drive_organization', '--only', 'content')
        file_obj.refresh_from_db()
        self.assertIsNone(file_obj.content_text)

    def test_activity_backfill_populates_legacy_canonical_columns(self):
        folder = Folder.objects.create(name='Legacy Folder', owner_id='user-1')
        file_obj = self._file(name='legacy.txt')
        file_obj.folder = folder
        file_obj.save(update_fields=['folder'])
        activity = Activity.objects.create(
            user_id='user-1',
            file=file_obj,
            action='download',
            details={'source': 'legacy'},
        )
        Activity.objects.filter(pk=activity.pk).update(
            actor_id='',
            event_type='',
            metadata={},
            resource_id=None,
            resource_type='',
            resource_name='',
            parent_id=None,
        )

        call_command('backfill_drive_organization', '--only', 'activity')

        activity.refresh_from_db()
        self.assertEqual(activity.actor_id, 'user-1')
        self.assertEqual(activity.event_type, 'download')
        self.assertEqual(activity.metadata, {'source': 'legacy'})
        self.assertEqual(activity.resource_id, file_obj.id)
        self.assertEqual(activity.resource_type, Activity.RESOURCE_FILE)
        self.assertEqual(activity.resource_name, file_obj.name)
        self.assertEqual(activity.parent_id, folder.id)

    def test_dry_run_does_not_write_content_or_activity(self):
        file_obj = self._file(content=b'dry run content')
        activity = Activity.objects.create(user_id='user-1', file=file_obj, action='view')
        Activity.objects.filter(pk=activity.pk).update(actor_id='', resource_id=None)

        call_command('backfill_drive_organization', '--dry-run')

        file_obj.refresh_from_db()
        activity.refresh_from_db()
        self.assertIsNone(file_obj.content_text)
        self.assertEqual(activity.actor_id, '')
        self.assertIsNone(activity.resource_id)
