"""DRV-36: canonical Drive activity/audit schema."""
import tempfile

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from drive.models import Activity, File, Folder

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv36-'))
class DriveActivitySchemaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='drv36', email='drv36@example.com', password='x')
        self.folder = Folder.objects.create(name='Audit Folder', owner_id=str(self.user.id))
        self.file = File.objects.create(
            name='audit.txt',
            owner_id=str(self.user.id),
            folder=self.folder,
            file=ContentFile(b'audit bytes', name='audit.txt'),
            file_type='document',
            mime_type='text/plain',
            size=11,
            extension='txt',
        )

    def test_record_event_inserts_and_query_helpers_return_all_canonical_fields(self):
        event_types = ['create', 'move', 'delete', 'share', 'download', 'restore']
        created = []
        base_time = timezone.now()
        for index, event_type in enumerate(event_types):
            created.append(Activity.record_event(
                event_type=event_type,
                actor_id=self.user.id,
                resource=self.file,
                metadata={'ordinal': index, 'event_type': event_type},
                timestamp=base_time + timezone.timedelta(seconds=index),
            ))

        by_resource = list(Activity.objects.for_resource(self.file.id))
        self.assertEqual([row.event_type for row in by_resource], list(reversed(event_types)))
        by_actor = list(Activity.objects.for_actor(self.user.id))
        self.assertEqual(len(by_actor), len(event_types))

        for row in created:
            row.refresh_from_db()
            self.assertEqual(row.actor_id, str(self.user.id))
            self.assertEqual(row.resource_id, self.file.id)
            self.assertEqual(row.resource_type, Activity.RESOURCE_FILE)
            self.assertEqual(row.resource_name, self.file.name)
            self.assertEqual(row.parent_id, self.folder.id)
            self.assertEqual(row.metadata['event_type'], row.event_type)
            self.assertIsNotNone(row.timestamp)

    def test_folder_resource_event_records_parent_snapshot(self):
        child = Folder.objects.create(name='Child', owner_id=str(self.user.id), parent=self.folder)
        event = Activity.record_event(
            event_type='move',
            actor_id=self.user.id,
            resource=child,
            metadata={'destination': str(self.folder.id)},
        )

        self.assertEqual(event.resource_type, Activity.RESOURCE_FOLDER)
        self.assertEqual(event.resource_id, child.id)
        self.assertEqual(event.resource_name, child.name)
        self.assertEqual(event.parent_id, self.folder.id)
        self.assertEqual(Activity.objects.for_resource(child.id).get().id, event.id)

    def test_legacy_activity_save_populates_canonical_columns(self):
        event = Activity.objects.create(
            user_id=str(self.user.id),
            file=self.file,
            action='download',
            details={'source': 'legacy-download-hook'},
        )

        event.refresh_from_db()
        self.assertEqual(event.actor_id, str(self.user.id))
        self.assertEqual(event.event_type, 'download')
        self.assertEqual(event.resource_id, self.file.id)
        self.assertEqual(event.resource_type, Activity.RESOURCE_FILE)
        self.assertEqual(event.resource_name, self.file.name)
        self.assertEqual(event.metadata, {'source': 'legacy-download-hook'})
        self.assertEqual(Activity.objects.for_actor(self.user.id).get().id, event.id)



class DriveActivityMigrationTests(TransactionTestCase):
    # MigrationExecutor manipulates schema state directly; TransactionTestCase
    # avoids wrapping the migration DDL in TestCase's atomic transaction.
    reset_sequences = True

    def tearDown(self):
        # This test deliberately migrates Drive back to 0010 and forward only to
        # 0011. Restore the current leaf so later TransactionTestCase classes see
        # the production schema (including VEN-151 tier/reservation tables).
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_backfills_timestamp_from_legacy_created_at(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('drive', '0010_merge_drv30_drv42')])
        legacy_apps = executor.loader.project_state([('drive', '0010_merge_drv30_drv42')]).apps
        LegacyActivity = legacy_apps.get_model('drive', 'Activity')

        legacy = LegacyActivity.objects.create(
            user_id='legacy-actor',
            action='download',
            details={'source': 'pre-drv36'},
        )
        original_created_at = timezone.now() - timezone.timedelta(days=3, minutes=7)
        LegacyActivity.objects.filter(pk=legacy.pk).update(created_at=original_created_at)

        executor.loader.build_graph()
        executor.migrate([('drive', '0011_drv36_activity_schema')])
        migrated_apps = executor.loader.project_state([('drive', '0011_drv36_activity_schema')]).apps
        MigratedActivity = migrated_apps.get_model('drive', 'Activity')

        migrated = MigratedActivity.objects.get(pk=legacy.pk)
        self.assertEqual(migrated.timestamp, original_created_at)
        self.assertEqual(migrated.actor_id, 'legacy-actor')
        self.assertEqual(migrated.event_type, 'download')
        self.assertEqual(migrated.metadata, {'source': 'pre-drv36'})
