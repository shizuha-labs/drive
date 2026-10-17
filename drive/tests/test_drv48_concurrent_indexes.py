"""DRV-48 (Slice 2): the index migration must be production-safe.

Static checks per the task's acceptance criteria: the migration is
non-atomic, every index is created/dropped CONCURRENTLY, the required HLD
DRV-24 §5 index names are present, and the operation is reversible.
These run on any DB vendor (they inspect the migration module, not the DB).
"""
import importlib

from django.db import migrations as dj_migrations
from django.test import SimpleTestCase

MIGRATION_MODULE = 'drive.migrations.0013_drv48_concurrent_search_indexes'

REQUIRED_INDEX_NAMES = [
    'drive_file_search_vector_gin',
    'drive_file_name_trgm',
    'drive_folder_name_trgm',
    'drive_share_shared_with_active',
    'drive_share_org_active',
    'drive_activity_actor_ts',
]


class DRV48IndexMigrationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module = importlib.import_module(MIGRATION_MODULE)

    def test_migration_is_not_atomic(self):
        """CONCURRENTLY cannot run inside a transaction block."""
        self.assertFalse(self.module.Migration.atomic)

    def test_all_required_index_names_present(self):
        declared = [name for name, _sql in self.module.DRV48_INDEXES]
        self.assertEqual(declared, REQUIRED_INDEX_NAMES)

    def test_every_create_statement_is_concurrent_and_idempotent(self):
        for name, sql in self.module.DRV48_INDEXES:
            with self.subTest(index=name):
                self.assertIn('CREATE INDEX CONCURRENTLY IF NOT EXISTS', sql)
                self.assertIn(name, sql)

    def test_trigram_extension_created_before_trigram_indexes(self):
        self.assertIn('CREATE EXTENSION IF NOT EXISTS pg_trgm',
                      self.module.CREATE_TRGM_EXTENSION)
        # Both trigram indexes actually use the trgm opclass.
        trgm = {name: sql for name, sql in self.module.DRV48_INDEXES
                if name.endswith('_trgm')}
        self.assertEqual(set(trgm), {'drive_file_name_trgm', 'drive_folder_name_trgm'})
        for sql in trgm.values():
            self.assertIn('gin_trgm_ops', sql)

    def test_fts_index_targets_drv47_search_vector(self):
        sql = dict(self.module.DRV48_INDEXES)['drive_file_search_vector_gin']
        self.assertIn('ON drive_file USING gin (search_vector)', sql)

    def test_share_and_activity_indexes_match_hld_predicates(self):
        by_name = dict(self.module.DRV48_INDEXES)
        self.assertIn('(shared_with_id, is_active)',
                      by_name['drive_share_shared_with_active'])
        self.assertIn('(organization_id, is_active)',
                      by_name['drive_share_org_active'])
        self.assertIn('(actor_id, timestamp DESC)',
                      by_name['drive_activity_actor_ts'])

    def test_migration_is_reversible(self):
        ops = self.module.Migration.operations
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertIsInstance(op, dj_migrations.RunPython)
        self.assertIs(op.code, self.module.create_indexes)
        self.assertIs(op.reverse_code, self.module.drop_indexes)

    def test_reverse_drops_concurrently_and_leaves_shared_extension(self):
        """PR #35 review P1: rollback drops ONLY this migration's six indexes
        (concurrently); the database-scoped shared pg_trgm extension must NOT
        be dropped — another app/index may depend on it and a failed
        DROP EXTENSION would abort the rollback half-reversed."""
        class FakePGSchemaEditor:
            class connection:
                vendor = 'postgresql'

            def __init__(self):
                self.executed = []

            def execute(self, sql):
                self.executed.append(sql)

        se = FakePGSchemaEditor()
        self.module.drop_indexes(None, se)

        drops = [sql for sql in se.executed
                 if sql.startswith('DROP INDEX CONCURRENTLY IF EXISTS')]
        self.assertEqual(len(drops), len(REQUIRED_INDEX_NAMES))
        for name in REQUIRED_INDEX_NAMES:
            self.assertTrue(any(name in sql for sql in drops), name)
        self.assertFalse(any('DROP EXTENSION' in sql for sql in se.executed))
        self.assertFalse(hasattr(self.module, 'DROP_TRGM_EXTENSION'))

    def test_non_postgres_vendors_are_skipped(self):
        """sqlite test harnesses must be able to migrate through this."""
        class FakeSE:
            class connection:
                vendor = 'sqlite'
            executed = []

            def execute(self, sql):
                self.executed.append(sql)

        se = FakeSE()
        self.module.create_indexes(None, se)
        self.module.drop_indexes(None, se)
        self.assertEqual(se.executed, [])
