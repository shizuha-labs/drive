"""
Django signals for Shizuha Drive
"""
import logging

from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from .models import File, Folder, StorageQuota

logger = logging.getLogger(__name__)


@receiver(post_save, sender=File)
def update_quota_on_file_create(sender, instance, created, **kwargs):
    """Update storage quota when file is created"""
    if created and not instance.is_trashed:
        quota, _ = StorageQuota.objects.get_or_create(owner_id=instance.owner_id)
        quota.add_file(instance.size)


@receiver(post_delete, sender=File)
def update_quota_on_file_delete(sender, instance, **kwargs):
    """Update storage quota when file is permanently deleted"""
    try:
        quota = StorageQuota.objects.get(owner_id=instance.owner_id)
        quota.remove_file(instance.size)
    except StorageQuota.DoesNotExist:
        pass


@receiver(post_save, sender=Folder)
def update_folder_count(sender, instance, created, **kwargs):
    """Update folder count in quota"""
    if created:
        quota, _ = StorageQuota.objects.get_or_create(owner_id=instance.owner_id)
        quota.folder_count += 1
        quota.save(update_fields=['folder_count'])


@receiver(post_delete, sender=Folder)
def decrease_folder_count(sender, instance, **kwargs):
    """Decrease folder count in quota"""
    try:
        quota = StorageQuota.objects.get(owner_id=instance.owner_id)
        quota.folder_count = max(0, quota.folder_count - 1)
        quota.save(update_fields=['folder_count'])
    except StorageQuota.DoesNotExist:
        pass


# --- PLAT-9234: storage-usage metering --------------------------------------

@receiver(pre_save, sender=StorageQuota)
def _capture_previous_used_bytes(sender, instance, **kwargs):
    """Remember the pre-mutation used_bytes so post_save can zero-suppress."""
    if instance.pk:
        try:
            previous = StorageQuota.objects.filter(pk=instance.pk).values_list(
                'used_bytes', flat=True).first()
        except Exception:
            previous = None
        instance._previous_used_bytes = previous
    else:
        instance._previous_used_bytes = None


@receiver(post_save, sender=StorageQuota)
def _emit_storage_usage_occurrence(sender, instance, created, **kwargs):
    """Emit the absolute per-owner storage gauge when used_bytes changes.

    Runs inside the caller's transaction (Django signals are synchronous),
    so the outbox row is durable the moment the quota mutation commits
    (HLD §5 transactional outbox).  Zero-delta updates (folder_count-only)
    emit nothing; the occurrence is replay-safe by event_id.
    """
    from .metering import storage_usage_occurrence

    try:
        storage_usage_occurrence(
            instance,
            previous_used_bytes=getattr(instance, '_previous_used_bytes', None),
        )
    except Exception:
        # A metering failure must never fail the product mutation; the
        # transactional outbox means only the enqueue is at risk here, and
        # the next quota change re-emits the absolute gauge (self-correcting).
        logger.warning(
            'PLAT-9234: storage usage occurrence enqueue failed for owner %s',
            instance.owner_id, exc_info=True,
        )
