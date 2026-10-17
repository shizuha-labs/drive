from django.core.management.base import BaseCommand

from drive.tasks import prune_file_versions


class Command(BaseCommand):
    help = 'Prune old FileVersion rows according to DRIVE_VERSION_RETENTION_COUNT.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--retention-count',
            type=int,
            default=None,
            help='Override DRIVE_VERSION_RETENTION_COUNT for this run.',
        )

    def handle(self, *args, **options):
        result = prune_file_versions.run(retention_count=options.get('retention_count'))
        self.stdout.write(self.style.SUCCESS(
            'Pruned {versions_pruned} versions across {files_seen} files (retention={retention_count})'.format(**result)
        ))
