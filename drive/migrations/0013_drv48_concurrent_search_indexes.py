"""DRV-48 (DRV-4 / HLD DRV-24 §5, Slice 2): production-safe search/organization
indexes.

Every index is created with ``CREATE INDEX CONCURRENTLY`` so the migration can
run against the live database without taking write locks — which requires
``atomic = False`` (CONCURRENTLY cannot run inside a transaction). Each
statement is idempotent (``IF NOT EXISTS`` / ``IF EXISTS``) and the whole
migration is reversible — the reverse drops ONLY the six indexes; the shared
``pg_trgm`` extension is deliberately left installed (see drop_indexes).

Scope notes (grounded in what Slice 1 / DRV-47 actually landed):
- ``GIN(drive_file.search_vector)`` — the FTS index for the DRV-47
  trigger-maintained vector.
- ``pg_trgm`` + GIN trigram on ``drive_file.name`` / ``drive_folder.name`` —
  substring/typo name recall replacing ``icontains`` sequential scans.
  Content stays FTS-only per the HLD (no content trigram).
- ``drive_share (shared_with_id, is_active)`` + ``(organization_id, is_active)``
  — the shared-with-me visibility predicate (HLD §9).
- ``drive_activity (actor_id, timestamp DESC)`` — per-actor activity feed
  (``ActivityQuerySet.for_actor`` orders by ``-timestamp``); the DRV-36 audit
  table is the activity store that exists today.
- NOT here: ``GIN(Folder.search_vector)`` (DRV-47 added ``search_vector`` to
  File only — the folder column does not exist yet) and the HLD's
  ``FileActivity``/``Star`` model indexes (those models are Slices 6/7; their
  indexes ship with their ``CreateModel``). The legacy per-owner star lookups
  are already covered by the existing ``(owner_id, is_starred)`` indexes.
"""

from django.db import migrations

# (name, CREATE statement) pairs — executed one at a time because
# CONCURRENTLY must run as a standalone statement outside a transaction.
DRV48_INDEXES = [
    (
        'drive_file_search_vector_gin',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS drive_file_search_vector_gin '
        'ON drive_file USING gin (search_vector);',
    ),
    (
        'drive_file_name_trgm',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS drive_file_name_trgm '
        'ON drive_file USING gin (name gin_trgm_ops);',
    ),
    (
        'drive_folder_name_trgm',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS drive_folder_name_trgm '
        'ON drive_folder USING gin (name gin_trgm_ops);',
    ),
    (
        'drive_share_shared_with_active',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS drive_share_shared_with_active '
        'ON drive_share (shared_with_id, is_active);',
    ),
    (
        'drive_share_org_active',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS drive_share_org_active '
        'ON drive_share (organization_id, is_active);',
    ),
    (
        'drive_activity_actor_ts',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS drive_activity_actor_ts '
        'ON drive_activity (actor_id, timestamp DESC);',
    ),
]

CREATE_TRGM_EXTENSION = 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'


def create_indexes(apps, schema_editor):
    # Postgres-only: sqlite/others (unit-test harnesses) have neither pg_trgm
    # nor CONCURRENTLY; the FTS/trigram query paths are Postgres-gated too.
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(CREATE_TRGM_EXTENSION)
    for _name, sql in DRV48_INDEXES:
        schema_editor.execute(sql)


def drop_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for name, _sql in reversed(DRV48_INDEXES):
        schema_editor.execute(
            f'DROP INDEX CONCURRENTLY IF EXISTS {name};')
    # pg_trgm is a DATABASE-scoped shared extension, not owned by this
    # migration: another app/index may depend on it, so an unconditional
    # DROP EXTENSION could fail mid-rollback AFTER our indexes were already
    # dropped, leaving the migration half-reversed (PR #35 review P1).
    # Leaving it installed on reverse is the safe pattern — CREATE ... IF NOT
    # EXISTS makes re-application idempotent either way.


class Migration(migrations.Migration):

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction block.
    atomic = False

    dependencies = [
        ('drive', '0012_drv47_content_search_vector'),
    ]

    operations = [
        migrations.RunPython(create_indexes, drop_indexes),
    ]
