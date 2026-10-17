"""DRV-52: idempotent Drive content/activity organization backfill.

This command is safe to run repeatedly. It intentionally does not fabricate
Activity rows for legacy files: audit history stays real, while existing legacy
Activity rows are normalized into the DRV-36 canonical columns.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from drive.models import Activity, File
from drive.tasks import (
    _document_kind,
    extract_searchable_content_text,
    is_plain_text_indexable,
)


ACTIVITY_BACKFILL_FIELDS = [
    'actor_id', 'event_type', 'metadata',
    'resource_id', 'resource_type', 'resource_name', 'parent_id',
]


class Command(BaseCommand):
    help = 'Backfill DRV-4 search content and DRV-36 activity organization columns.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report counts without writing rows.')
        parser.add_argument(
            '--only',
            choices=['content', 'activity'],
            help='Limit the backfill to one surface. By default both are processed.',
        )
        parser.add_argument('--batch-size', type=int, default=200, help='Rows per database batch (default 200).')
        parser.add_argument(
            '--include-documents',
            action='store_true',
            help='Also extract PDF/OOXML documents. Default limits content backfill to cheap plain-text/code files.',
        )

    def handle(self, *args, **options):
        batch_size = max(1, options['batch_size'])
        dry_run = options['dry_run']
        only = options.get('only')
        summary = {
            'content_seen': 0,
            'content_updated': 0,
            'content_skipped_documents': 0,
            'activity_seen': 0,
            'activity_updated': 0,
            'dry_run': dry_run,
        }

        if only in (None, 'content'):
            summary.update(self._backfill_content(
                dry_run=dry_run,
                batch_size=batch_size,
                include_documents=options['include_documents'],
            ))
        if only in (None, 'activity'):
            summary.update(self._backfill_activity(dry_run=dry_run, batch_size=batch_size))

        self.stdout.write(
            'drive-organization-backfill: '
            'content_seen={content_seen} content_updated={content_updated} '
            'content_skipped_documents={content_skipped_documents} '
            'activity_seen={activity_seen} activity_updated={activity_updated} '
            'dry_run={dry_run}'.format(**summary)
        )

    def _backfill_content(self, *, dry_run, batch_size, include_documents):
        qs = (
            File.objects
            .filter(content_text__isnull=True, is_trashed=False)
            .order_by('id')
        )
        seen = updated = skipped_documents = 0
        for file_obj in qs.iterator(chunk_size=batch_size):
            seen += 1
            document_kind = _document_kind(file_obj)
            if (
                not include_documents
                and document_kind is not None
                and not is_plain_text_indexable(file_obj)
            ):
                skipped_documents += 1
                continue
            if dry_run:
                updated += 1
                continue
            try:
                content_text = extract_searchable_content_text(file_obj)
                processing_error = ''
            except Exception as exc:  # noqa: BLE001 — record and continue; do not stop the batch.
                content_text = ''
                processing_error = f'content_text backfill failed: {exc.__class__.__name__}'
            if content_text is None:
                content_text = ''
            File.objects.filter(pk=file_obj.pk).update(
                content_text=content_text,
                processing_error=processing_error,
                is_processed=True,
            )
            updated += 1
        return {
            'content_seen': seen,
            'content_updated': updated,
            'content_skipped_documents': skipped_documents,
        }

    def _backfill_activity(self, *, dry_run, batch_size):
        qs = (
            Activity.objects
            .filter(
                Q(resource_id__isnull=True)
                | Q(resource_type='')
                | Q(actor_id='')
                | Q(event_type='')
                | Q(resource_name='')
            )
            .select_related('file', 'folder')
            .order_by('id')
        )
        seen = updated = 0
        pending = []
        for activity in qs.iterator(chunk_size=batch_size):
            seen += 1
            if self._populate_activity(activity):
                updated += 1
                pending.append(activity)
            if len(pending) >= batch_size:
                self._flush_activity(pending, dry_run=dry_run)
                pending = []
        self._flush_activity(pending, dry_run=dry_run)
        return {'activity_seen': seen, 'activity_updated': updated}

    def _flush_activity(self, rows, *, dry_run):
        if not rows or dry_run:
            return
        with transaction.atomic():
            Activity.objects.bulk_update(rows, ACTIVITY_BACKFILL_FIELDS)

    def _populate_activity(self, activity):
        before = tuple(getattr(activity, field) for field in ACTIVITY_BACKFILL_FIELDS)
        if not activity.actor_id and activity.user_id:
            activity.actor_id = str(activity.user_id)
        if not activity.event_type and activity.action:
            activity.event_type = activity.action
        if not activity.metadata and activity.details:
            activity.metadata = activity.details
        if activity.file_id:
            activity.resource_id = activity.resource_id or activity.file_id
            activity.resource_type = activity.resource_type or Activity.RESOURCE_FILE
            if not activity.resource_name and activity.file:
                activity.resource_name = activity.file.name
            if not activity.parent_id and activity.file and activity.file.folder_id:
                activity.parent_id = activity.file.folder_id
        elif activity.folder_id:
            activity.resource_id = activity.resource_id or activity.folder_id
            activity.resource_type = activity.resource_type or Activity.RESOURCE_FOLDER
            if not activity.resource_name and activity.folder:
                activity.resource_name = activity.folder.name
            if not activity.parent_id and activity.folder and activity.folder.parent_id:
                activity.parent_id = activity.folder.parent_id
        after = tuple(getattr(activity, field) for field in ACTIVITY_BACKFILL_FIELDS)
        return before != after
