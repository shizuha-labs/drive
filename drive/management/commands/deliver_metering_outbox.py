"""Deliver PENDING Drive metering occurrences to the Metering Core (at-least-once).

PLAT-9234 / cortex#211 pattern: wire this into celery-beat (or cron) at a
short interval — every quota mutation is already durable in the local
outbox, so delivery lag is the only exposure and it is monitored via
``drive.metering_delivery.outbox_age_seconds``.
"""
from django.core.management.base import BaseCommand

from drive import metering_delivery


class Command(BaseCommand):
    help = 'Deliver PENDING occurrence-outbox rows to the Metering Core (replay-safe).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, **options):
        stats = metering_delivery.deliver_pending(limit=options['limit'])
        self.stdout.write(
            'metering delivery: delivered={delivered} failed={failed} '
            'conflict={conflict} unavailable={unavailable}'.format(**stats)
        )
