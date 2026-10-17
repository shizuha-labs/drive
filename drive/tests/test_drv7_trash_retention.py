"""
DRV-7: trash retention + auto-purge.

- Trashed items older than DRIVE_TRASH_RETENTION_DAYS are permanently purged by
  the scheduled job; items inside the window survive and stay restorable.
- Trashed items expose `days_until_purge`.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from drive.models import File
from drive.tasks import purge_expired_trash
from drive.serializers import FileSerializer


class TrashRetentionTests(TestCase):
    def _file(self, name, trashed_days_ago=None, owner_id='1'):
        f = File.objects.create(
            owner_id=owner_id, name=name, mime_type='text/plain', size=10)
        if trashed_days_ago is not None:
            f.is_trashed = True
            f.trashed_at = timezone.now() - timedelta(days=trashed_days_ago)
            f.save(update_fields=['is_trashed', 'trashed_at'])
        return f

    @override_settings(DRIVE_TRASH_RETENTION_DAYS=30)
    def test_purge_removes_past_retention_keeps_in_window(self):
        old = self._file('old', trashed_days_ago=31)       # past 30d → purge
        recent = self._file('recent', trashed_days_ago=5)  # in-window → keep
        active = self._file('active')                       # not trashed → keep
        res = purge_expired_trash()
        self.assertEqual(res['purged_files'], 1)
        self.assertEqual(res['retention_days'], 30)
        self.assertFalse(File.objects.filter(pk=old.pk).exists())
        self.assertTrue(File.objects.filter(pk=recent.pk).exists())
        self.assertTrue(File.objects.filter(pk=active.pk).exists())

    @override_settings(DRIVE_TRASH_RETENTION_DAYS=30)
    def test_in_window_item_still_restorable_after_purge(self):
        recent = self._file('recent2', trashed_days_ago=5)
        purge_expired_trash()
        recent.refresh_from_db()  # survived purge
        recent.restore_from_trash()
        recent.refresh_from_db()
        self.assertFalse(recent.is_trashed)
        self.assertIsNone(recent.trashed_at)

    @override_settings(DRIVE_TRASH_RETENTION_DAYS=30)
    def test_days_until_purge_surfaced(self):
        recent = self._file('r3', trashed_days_ago=10)  # 20 days remain
        active = self._file('a3')
        self.assertEqual(FileSerializer(recent).data['days_until_purge'], 20)
        self.assertIsNone(FileSerializer(active).data['days_until_purge'])

    @override_settings(DRIVE_TRASH_RETENTION_DAYS=30)
    def test_days_until_purge_floors_at_zero(self):
        overdue = self._file('r4', trashed_days_ago=40)  # past retention
        self.assertEqual(FileSerializer(overdue).data['days_until_purge'], 0)

    @override_settings(DRIVE_TRASH_RETENTION_DAYS=7)
    def test_retention_setting_respected(self):
        f = self._file('r5', trashed_days_ago=8)  # past the 7-day window
        res = purge_expired_trash()
        self.assertEqual(res['retention_days'], 7)
        self.assertFalse(File.objects.filter(pk=f.pk).exists())
