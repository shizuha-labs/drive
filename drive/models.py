"""
Shizuha Drive Models - File Storage Service

Models:
- Folder: Hierarchical folder structure
- File: Individual files with metadata
- FileVersion: Version history for files
- Share: Sharing permissions (link and user-based)
- StorageQuota: User storage quotas
"""

import uuid
import os
import calendar
from datetime import timedelta
from django.db import models, transaction
from django.db.models.functions import Greatest, Lower
from django.contrib.postgres.search import SearchVectorField
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.utils import timezone


def file_upload_path(instance, filename):
    """Generate upload path: files/{owner_id}/{year}/{month}/{uuid}_{filename}"""
    now = timezone.now()
    return f"files/{instance.owner_id}/{now.year}/{now.month:02d}/{instance.id}_{filename}"


def thumbnail_upload_path(instance, filename):
    """Generate thumbnail path: thumbnails/{owner_id}/{file_id}_{size}.{ext}"""
    return f"thumbnails/{instance.file.owner_id}/{instance.file.id}_{instance.size}.jpg"


def drive_trash_restore_window_days():
    """Configured trash restore window, defaulting to the retention setting."""
    configured = getattr(settings, 'DRIVE_TRASH_RESTORE_WINDOW_DAYS', None)
    if configured is not None:
        return configured
    return getattr(settings, 'DRIVE_TRASH_RETENTION_DAYS', 30)


class Folder(models.Model):
    """
    Hierarchical folder structure for organizing files.
    Root folder has parent=None.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=64, db_index=True, help_text="User ID from Shizuha ID")
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )

    # Metadata
    color = models.CharField(max_length=7, blank=True, help_text="Hex color for folder icon")
    description = models.TextField(blank=True)

    # Starred/Pinned
    is_starred = models.BooleanField(default=False)

    # Soft delete (trash)
    is_trashed = models.BooleanField(default=False)
    trashed_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['owner_id', 'parent']),
            models.Index(fields=['owner_id', 'is_trashed']),
            models.Index(fields=['owner_id', 'is_starred']),
        ]
        unique_together = [['owner_id', 'parent', 'name']]

    def __str__(self):
        return self.name

    @property
    def path(self):
        """Get full path from root"""
        parts = [self.name]
        parent = self.parent
        while parent:
            parts.insert(0, parent.name)
            parent = parent.parent
        return '/' + '/'.join(parts)

    def get_ancestors(self):
        """Get all ancestor folders"""
        ancestors = []
        parent = self.parent
        while parent:
            ancestors.insert(0, parent)
            parent = parent.parent
        return ancestors

    def get_descendants(self):
        """Get all descendant folders (recursive)"""
        descendants = list(self.children.all())
        for child in self.children.all():
            descendants.extend(child.get_descendants())
        return descendants

    def move_to_trash(self):
        """Move folder and all contents to trash"""
        self.is_trashed = True
        self.trashed_at = timezone.now()
        self.save()
        # Trash all files in this folder
        self.files.update(is_trashed=True, trashed_at=timezone.now())
        # Recursively trash subfolders
        for child in self.children.all():
            child.move_to_trash()


class FileType(models.TextChoices):
    """File type categories"""
    DOCUMENT = 'document', 'Document'
    SPREADSHEET = 'spreadsheet', 'Spreadsheet'
    PRESENTATION = 'presentation', 'Presentation'
    IMAGE = 'image', 'Image'
    VIDEO = 'video', 'Video'
    AUDIO = 'audio', 'Audio'
    ARCHIVE = 'archive', 'Archive'
    CODE = 'code', 'Code'
    OTHER = 'other', 'Other'


class File(models.Model):
    """
    Individual file with metadata and version history.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=64, db_index=True, help_text="User ID from Shizuha ID")
    folder = models.ForeignKey(
        Folder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='files'
    )

    # File storage
    file = models.FileField(upload_to=file_upload_path)

    # File metadata
    file_type = models.CharField(max_length=20, choices=FileType.choices, default=FileType.OTHER)
    mime_type = models.CharField(max_length=100)
    size = models.BigIntegerField(help_text="File size in bytes")
    extension = models.CharField(max_length=20, blank=True)

    # Checksums for integrity. checksum/checksum_algorithm are the canonical
    # DRV-5 metadata; checksum_sha256 is kept for backward-compatible callers.
    checksum_md5 = models.CharField(max_length=32, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    checksum_algorithm = models.CharField(max_length=16, default='sha256')

    # Image/Video specific
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration = models.PositiveIntegerField(null=True, blank=True, help_text="Duration in seconds for audio/video")

    # Thumbnail
    has_thumbnail = models.BooleanField(default=False)

    # Starred/Pinned
    is_starred = models.BooleanField(default=False)

    # Processing status
    is_processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True)

    # DRV-4 Slice 1: capped plain-text search cache plus trigger-maintained
    # PostgreSQL tsvector. content_text is a derived cache only; canonical bytes
    # stay in object storage and this field must never be logged.
    content_text = models.TextField(null=True, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)

    # Soft delete (trash)
    is_trashed = models.BooleanField(default=False)
    trashed_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.CharField(max_length=64, blank=True, default='')
    restore_until = models.DateTimeField(null=True, blank=True, db_index=True)

    # Version control
    version = models.PositiveIntegerField(default=1)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['owner_id', 'folder']),
            models.Index(fields=['owner_id', 'is_trashed']),
            models.Index(fields=['owner_id', 'is_starred']),
            models.Index(fields=['owner_id', 'file_type']),
            models.Index(fields=['mime_type']),
            models.Index(fields=['-updated_at']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # DRV-5: keep legacy and canonical SHA-256 metadata in sync.
        if self.checksum_sha256 and not self.checksum:
            self.checksum = self.checksum_sha256
        elif self.checksum and not self.checksum_sha256 and self.checksum_algorithm == 'sha256':
            self.checksum_sha256 = self.checksum
        if self.checksum and not self.checksum_algorithm:
            self.checksum_algorithm = 'sha256'
        super().save(*args, **kwargs)

    @property
    def path(self):
        """Get full path including folder"""
        if self.folder:
            return f"{self.folder.path}/{self.name}"
        return f"/{self.name}"

    @property
    def size_human(self):
        """Human-readable file size"""
        size = self.size
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def move_to_trash(self, deleted_by=None):
        """Move file to trash"""
        now = timezone.now()
        self.is_trashed = True
        self.trashed_at = now
        self.deleted_at = now
        self.deleted_by = str(deleted_by or '')
        self.restore_until = now + timedelta(days=drive_trash_restore_window_days())
        self.save(update_fields=['is_trashed', 'trashed_at', 'deleted_at', 'deleted_by', 'restore_until', 'updated_at'])

    def restore_from_trash(self):
        """Restore file from trash"""
        now = timezone.now()
        if not self.is_trashed:
            return
        if self.restore_until and self.restore_until <= now:
            raise ValidationError({'restore_until': 'Trash restore window has expired; file must be purged.'})
        self.is_trashed = False
        self.trashed_at = None
        self.deleted_at = None
        self.deleted_by = ''
        self.restore_until = None
        self.save(update_fields=['is_trashed', 'trashed_at', 'deleted_at', 'deleted_by', 'restore_until', 'updated_at'])

    def delete_permanently(self):
        """Permanently delete this file's storage blob(s) and DB record.

        VEN-153 review fix: delete the storage blob(s) BEFORE the DB row and let a
        storage failure propagate — the previous order (row first, then blob)
        meant a failed `storage.delete` left the blob orphaned with no File row to
        retry from, while the DeletionRequest stayed pending. With blob-first, a
        storage failure raises before the row is removed, so the row survives for
        the next retry beat. Reference-existence checks exclude this file's OWN
        rows (they still exist at this point, unlike the old post-delete order).
        """
        storage = self.file.storage if self.file else None
        paths = set()
        if self.file and self.file.name:
            paths.add(self.file.name)
        version_pks = []
        for version in self.versions.all():
            version_pks.append(version.pk)
            if version.file_data and version.file_data.name:
                paths.add(version.file_data.name)
        thumbnail_pks = []
        for thumbnail in self.thumbnails.all():
            thumbnail_pks.append(thumbnail.pk)
            if thumbnail.image and thumbnail.image.name:
                paths.add(thumbnail.image.name)

        # 1) Delete blobs first. If any delete raises, we stop BEFORE removing the
        #    DB row so this File is retried on the next beat. Skip a path that any
        #    OTHER (unrelated) row still references — excluding this file's own
        #    rows, which are still present right now.
        if storage:
            for file_path in paths:
                still_referenced = (
                    File.objects.filter(file=file_path).exclude(pk=self.pk).exists()
                    or FileVersion.objects.filter(file_data=file_path).exclude(pk__in=version_pks).exists()
                    or Thumbnail.objects.filter(image=file_path).exclude(pk__in=thumbnail_pks).exists()
                )
                if not still_referenced:
                    storage.delete(file_path)  # may raise → row NOT deleted, retried next beat

        # 2) Blobs are gone (or still referenced elsewhere) → remove DB records.
        self.shares.all().delete()
        super().delete()


class ResumableUpload(models.Model):
    """Chunked upload session for large files (DRV-10)."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        ABORTED = 'aborted', 'Aborted'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.CharField(max_length=64, db_index=True, help_text="User ID from Shizuha ID")
    folder = models.ForeignKey(Folder, on_delete=models.SET_NULL, null=True, blank=True, related_name='resumable_uploads')
    name = models.CharField(max_length=255)
    size = models.BigIntegerField(help_text="Expected final size in bytes")
    mime_type = models.CharField(max_length=100, blank=True)
    chunk_size = models.PositiveIntegerField(default=5 * 1024 * 1024)
    uploaded_bytes = models.BigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    temp_path = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True, related_name='upload_sessions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['owner_id', 'status']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"ResumableUpload({self.id}, {self.owner_id}, {self.status})"

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()


class FileVersion(models.Model):
    """
    Version history for files.
    When a file is updated, previous version is stored here.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='versions')
    version_number = models.PositiveIntegerField()

    # File storage (old version)
    file_data = models.FileField(upload_to='versions/')
    size = models.BigIntegerField()
    checksum_md5 = models.CharField(max_length=32, blank=True)

    # Who made this version
    created_by_id = models.CharField(max_length=64, help_text="User ID who created this version")
    created_at = models.DateTimeField(auto_now_add=True)

    # Optional comment
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ['-version_number']
        unique_together = [['file', 'version_number']]

    def __str__(self):
        return f"{self.file.name} v{self.version_number}"


class Thumbnail(models.Model):
    """
    Thumbnails for image/video files in multiple sizes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='thumbnails')

    SIZE_CHOICES = [
        ('small', 'Small (128x128)'),
        ('medium', 'Medium (256x256)'),
        ('large', 'Large (512x512)'),
    ]
    size = models.CharField(max_length=10, choices=SIZE_CHOICES)
    image = models.ImageField(upload_to=thumbnail_upload_path)

    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['file', 'size']]

    def __str__(self):
        return f"{self.file.name} - {self.size}"


class ShareType(models.TextChoices):
    """Types of sharing"""
    LINK = 'link', 'Public Link'
    USER = 'user', 'Specific User'
    ORGANIZATION = 'org', 'Organization'


class SharePermission(models.TextChoices):
    """Permission levels for shares"""
    VIEW = 'view', 'View Only'
    COMMENT = 'comment', 'Can Comment'
    EDIT = 'edit', 'Can Edit'


class Share(models.Model):
    """
    Sharing settings for files and folders.
    Supports both public links and user-specific sharing.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # What's being shared (either file or folder)
    file = models.ForeignKey(File, on_delete=models.CASCADE, null=True, blank=True, related_name='shares')
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, null=True, blank=True, related_name='shares')

    # Share type
    share_type = models.CharField(max_length=10, choices=ShareType.choices)
    permission = models.CharField(max_length=10, choices=SharePermission.choices, default=SharePermission.VIEW)

    # For link shares
    link_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    link_password = models.CharField(max_length=128, blank=True, help_text="Hashed password")

    # For user shares
    shared_with_id = models.CharField(max_length=64, null=True, blank=True, help_text="User ID to share with")
    shared_with_email = models.EmailField(blank=True, help_text="Email if user doesn't exist yet")

    # For organization shares
    organization_id = models.CharField(max_length=64, null=True, blank=True, help_text="Organization ID from Shizuha Admin")

    # Owner/creator
    created_by_id = models.CharField(max_length=64, help_text="User ID who created this share")

    # Expiration
    expires_at = models.DateTimeField(null=True, blank=True)

    # Access tracking
    access_count = models.PositiveIntegerField(default=0)
    last_accessed_at = models.DateTimeField(null=True, blank=True)

    # Settings
    allow_download = models.BooleanField(default=True)
    notify_on_access = models.BooleanField(default=False)
    # DRV-14 INV-4: cross-org shares must be made explicit; default-deny.
    allow_cross_org = models.BooleanField(default=False, help_text="Explicitly permits a cross-org share (INV-4)")

    # Status
    is_active = models.BooleanField(default=True)
    # DRV-13 M-4: soft-revoke timestamp. A revoked share keeps its row (is_active
    # = False) so referencing audit logs (ShareAccessLog, P5) survive (INV-5);
    # revoked_at records WHEN it was revoked.
    revoked_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['link_token']),
            models.Index(fields=['shared_with_id']),
            models.Index(fields=['organization_id']),
            models.Index(fields=['file', 'is_active']),
            models.Index(fields=['folder', 'is_active']),
        ]

    def __str__(self):
        target = self.file.name if self.file else self.folder.name
        return f"Share: {target} ({self.share_type})"

    @property
    def is_expired(self):
        """Check if share has expired"""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False

    def record_access(self):
        """Record an access to this share"""
        self.access_count += 1
        self.last_accessed_at = timezone.now()
        self.save(update_fields=['access_count', 'last_accessed_at'])


class StorageQuota(models.Model):
    """
    Storage quota tracking per user.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.CharField(max_length=64, unique=True, help_text="User ID from Shizuha ID")

    # Quota settings
    quota_bytes = models.BigIntegerField(default=5368709120, help_text="Storage quota in bytes (default 5GB)")
    used_bytes = models.BigIntegerField(default=0, help_text="Currently used storage in bytes")

    # File counts
    file_count = models.PositiveIntegerField(default=0)
    folder_count = models.PositiveIntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Storage quotas"

    def __str__(self):
        return f"Quota for {self.owner_id}: {self.used_human} / {self.quota_human}"

    @property
    def used_human(self):
        """Human-readable used storage"""
        size = self.used_bytes
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @property
    def quota_human(self):
        """Human-readable quota"""
        size = self.quota_bytes
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @property
    def usage_percent(self):
        """Usage percentage"""
        if self.quota_bytes == 0:
            return 0
        return (self.used_bytes / self.quota_bytes) * 100

    @property
    def remaining_bytes(self):
        """Remaining storage in bytes (Drive usage only, excludes service usage)"""
        return max(0, self.quota_bytes - self.used_bytes)

    @property
    def total_used_bytes(self):
        """Total used bytes across Drive + all services"""
        service_total = ServiceStorageUsage.objects.filter(
            owner_id=self.owner_id
        ).aggregate(total=models.Sum('used_bytes'))['total'] or 0
        return self.used_bytes + service_total

    @property
    def total_used_human(self):
        """Human-readable unified usage across Drive and external services."""
        size = self.total_used_bytes
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @property
    def total_usage_percent(self):
        """Unified usage percentage used by admission and customer meters."""
        if self.quota_bytes == 0:
            return 0
        return (self.total_used_bytes / self.quota_bytes) * 100

    @property
    def total_remaining_bytes(self):
        """Remaining storage accounting for services and active reservations."""
        return max(0, self.quota_bytes - self.total_used_bytes - self.active_reserved_bytes)

    @property
    def active_reserved_bytes(self):
        """Bytes held by unexpired admissions that have not committed yet."""
        if not self.pk:
            return 0
        return StorageQuotaReservation.objects.filter(
            owner_id=self.owner_id,
            status=StorageQuotaReservation.Status.ACTIVE,
            expires_at__gt=timezone.now(),
        ).aggregate(total=models.Sum('size_bytes'))['total'] or 0

    def can_upload(self, file_size):
        """Read-only capacity check; callers that commit bytes must use reserve_upload."""
        self._sync_tier_quota()
        return self.total_remaining_bytes >= file_size

    @classmethod
    def reserve_upload(cls, owner_id, file_size, *, ttl_seconds=900):
        """Atomically hold quota for an upload.

        The quota row is the serialization point for every admission. The caller
        must keep its surrounding transaction open through the corresponding
        File/service-usage commit, then mark the returned reservation committed.
        A rollback removes both the hold and the resource mutation.
        """
        owner_id = str(owner_id)
        file_size = int(file_size)
        if file_size < 0:
            raise ValueError("file_size must be non-negative")
        ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        with transaction.atomic():
            cls.objects.get_or_create(owner_id=owner_id)
            quota = cls.objects.select_for_update().get(owner_id=owner_id)
            quota._sync_tier_quota()
            if quota.total_remaining_bytes < file_size:
                return None, quota
            reservation = StorageQuotaReservation.objects.create(
                owner_id=owner_id,
                size_bytes=file_size,
                expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
            )
            return reservation, quota

    def _sync_tier_quota(self):
        """Sync quota_bytes from the user's tier assignment, if any.

        When a UserTier record exists, its quota_bytes overrides the default
        StorageQuota.quota_bytes so that pro/team users get their full tier
        allowance rather than the free 5GB default.
        """
        try:
            user_tier = UserTier.objects.get(owner_id=self.owner_id)
            if user_tier.quota_bytes != self.quota_bytes:
                self.quota_bytes = user_tier.quota_bytes
                self.save(update_fields=['quota_bytes', 'updated_at'])
        except UserTier.DoesNotExist:
            pass

    def add_file(self, file_size):
        """Atomically account for a committed file without lost updates."""
        type(self).objects.filter(pk=self.pk).update(
            used_bytes=models.F('used_bytes') + int(file_size),
            file_count=models.F('file_count') + 1,
            updated_at=timezone.now(),
        )
        self.refresh_from_db(fields=['used_bytes', 'file_count', 'updated_at'])

    def remove_file(self, file_size):
        """Atomically account for deletion while flooring counters at zero."""
        type(self).objects.filter(pk=self.pk).update(
            used_bytes=Greatest(models.Value(0), models.F('used_bytes') - int(file_size)),
            file_count=Greatest(models.Value(0), models.F('file_count') - 1),
            updated_at=timezone.now(),
        )
        self.refresh_from_db(fields=['used_bytes', 'file_count', 'updated_at'])

    # DRV-23 (VEN-49): the limited FREE external-validation quota tier. The model
    # default is already 5GB; this names it so the free-tier provisioning is
    # explicit and configurable in one place (no payment / paid upgrade).
    FREE_EXTERNAL_QUOTA_BYTES = 5 * 1024 ** 3  # 5 GiB

    @classmethod
    def provision_free(cls, owner_id):
        """Idempotently ensure an external validation user has the free quota tier.

        Quota is already auto-created at 5GB on first upload/access via
        get_or_create; this is the explicit admin/onboarding hook for accepting an
        external validation user (DRV-23 acceptance: applied automatically or via
        a documented workflow). Returns (quota, created)."""
        return cls.objects.get_or_create(
            owner_id=str(owner_id),
            defaults={'quota_bytes': cls.FREE_EXTERNAL_QUOTA_BYTES},
        )


class ServiceStorageUsage(models.Model):
    """
    Track storage usage reported by external services (wiki, notes, finance, inventory).
    Each service reports its per-user usage here so Drive can enforce unified quotas.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.CharField(max_length=64, help_text="User ID from Shizuha ID")
    service = models.CharField(max_length=50, help_text="Service name (e.g. wiki, notes, finance)")
    used_bytes = models.BigIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['owner_id', 'service']]
        verbose_name_plural = "Service storage usage"
        indexes = [
            models.Index(fields=['owner_id']),
        ]

    def __str__(self):
        return f"{self.service}: {self.owner_id} ({self.used_bytes} bytes)"


class StorageQuotaReservation(models.Model):
    """Short-lived, atomic storage admission hold."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        COMMITTED = 'committed', 'Committed'
        RELEASED = 'released', 'Released'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.CharField(max_length=64)
    size_bytes = models.BigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    expires_at = models.DateTimeField()
    committed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['owner_id', 'status', 'expires_at'],
                name='drive_quota_res_active_idx',
            ),
        ]

    def mark_committed(self):
        if self.status != self.Status.ACTIVE:
            return
        self.status = self.Status.COMMITTED
        self.committed_at = timezone.now()
        self.save(update_fields=['status', 'committed_at', 'updated_at'])

    def release(self):
        if self.status != self.Status.ACTIVE:
            return
        self.status = self.Status.RELEASED
        self.released_at = timezone.now()
        self.save(update_fields=['status', 'released_at', 'updated_at'])


class ActivityQuerySet(models.QuerySet):
    """Canonical Drive activity/audit query helpers (DRV-36)."""

    def for_resource(self, resource_id):
        return self.filter(resource_id=resource_id).order_by('-timestamp')

    def for_actor(self, actor_id):
        return self.filter(actor_id=str(actor_id)).order_by('-timestamp')


class StorageTier(models.Model):
    """
    Storage tier definitions with quota limits.
    Free: 5GB, Pro: 50GB, Team: 200GB
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True, help_text="Tier name (free, pro, team)")
    slug = models.SlugField(max_length=50, unique=True, help_text="URL-friendly identifier")
    quota_bytes = models.BigIntegerField(help_text="Storage quota in bytes")
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                         help_text="Monthly price in INR")
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                        help_text="Yearly price in INR")
    description = models.TextField(blank=True, help_text="Tier description")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0, help_text="Display order")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order']

    def __str__(self):
        return f"{self.name} ({self.quota_human})"

    @property
    def quota_human(self):
        size = self.quota_bytes
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.0f} {unit}"
            size /= 1024
        return f"{size:.0f} PB"

    @property
    def soft_limit_bytes(self):
        """90% of quota — soft limit warning threshold"""
        return int(self.quota_bytes * 0.9)


class UserTier(models.Model):
    """
    Maps users to storage tiers.
    Supports upgrade/downgrade with next_billing_cycle scheduling.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.CharField(max_length=64, unique=True, help_text="User ID from Shizuha ID")
    tier = models.ForeignKey(StorageTier, on_delete=models.PROTECT, related_name='users')
    quota_bytes = models.BigIntegerField(help_text="Effective quota in bytes (from tier or custom)")

    # Upgrade/downgrade scheduled for next billing cycle
    pending_tier = models.ForeignKey(
        StorageTier, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pending_users'
    )
    pending_quota_bytes = models.BigIntegerField(null=True, blank=True,
                                                  help_text="Pending quota for next billing cycle")

    # Billing
    billing_cycle = models.CharField(
        max_length=10, choices=[('monthly', 'Monthly'), ('yearly', 'Yearly')],
        default='monthly'
    )
    next_billing_date = models.DateTimeField(null=True, blank=True)
    pending_entitlement_reference = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Trusted billing entitlement authorizing a paid pending tier",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "User tiers"
        indexes = [
            models.Index(fields=['owner_id'], name='drive_usert_owner_i_idx'),
            models.Index(fields=['tier'], name='drive_usert_tier_id_idx'),
        ]

    def __str__(self):
        return f"{self.owner_id} → {self.tier.name}"

    @staticmethod
    def next_cycle_date(reference, billing_cycle):
        """Return the deterministic next monthly/yearly boundary."""
        if billing_cycle == 'yearly':
            year, month = reference.year + 1, reference.month
        else:
            year = reference.year + (1 if reference.month == 12 else 0)
            month = 1 if reference.month == 12 else reference.month + 1
        day = min(reference.day, calendar.monthrange(year, month)[1])
        return reference.replace(year=year, month=month, day=day)

    def apply_pending_changes(self, *, now=None):
        """Apply a due change if it is free or backed by trusted entitlement."""
        now = now or timezone.now()
        if not self.pending_tier or self.pending_quota_bytes is None:
            return False
        if self.next_billing_date is None or self.next_billing_date > now:
            return False
        if self.pending_tier.price_monthly > 0 and not self.pending_entitlement_reference:
            return False

        self.tier = self.pending_tier
        self.quota_bytes = self.pending_quota_bytes
        self.pending_tier = None
        self.pending_quota_bytes = None
        self.pending_entitlement_reference = None
        self.next_billing_date = self.next_cycle_date(now, self.billing_cycle)
        self.save(update_fields=[
            'tier', 'quota_bytes', 'pending_tier', 'pending_quota_bytes',
            'pending_entitlement_reference', 'next_billing_date', 'updated_at',
        ])
        StorageQuota.objects.filter(owner_id=self.owner_id).update(
            quota_bytes=self.quota_bytes,
            updated_at=now,
        )
        return True

    def schedule_change(self, new_tier, new_quota_bytes, *, now=None):
        """Record an intent at the trusted, immutable billing boundary.

        The public caller never chooses ``billing_cycle`` or moves an existing
        boundary. Repeated downgrade requests therefore cannot postpone the
        effective date.
        """
        now = now or timezone.now()
        derived_boundary = self.next_cycle_date(now, self.billing_cycle)
        if self.pending_tier_id and self.next_billing_date:
            effective_boundary = self.next_billing_date
        elif self.next_billing_date and self.next_billing_date > now:
            effective_boundary = min(self.next_billing_date, derived_boundary)
        else:
            effective_boundary = derived_boundary
        self.pending_tier = new_tier
        self.pending_quota_bytes = new_quota_bytes
        self.pending_entitlement_reference = None
        self.next_billing_date = effective_boundary
        self.save(update_fields=[
            'pending_tier', 'pending_quota_bytes', 'pending_entitlement_reference',
            'next_billing_date', 'updated_at',
        ])

    def authorize_pending_change(self, entitlement_reference):
        """Attach a trusted payment/entitlement event to a pending paid change."""
        if not self.pending_tier or self.pending_tier.price_monthly <= 0:
            raise ValidationError("No paid pending tier requires entitlement")
        if not entitlement_reference:
            raise ValidationError("A trusted entitlement reference is required")
        self.pending_entitlement_reference = str(entitlement_reference)
        self.save(update_fields=['pending_entitlement_reference', 'updated_at'])

    @property
    def soft_limit_bytes(self):
        """90% of effective quota"""
        return int(self.quota_bytes * 0.9)

    @property
    def usage_percent(self):
        """Usage percentage against effective quota"""
        if self.quota_bytes == 0:
            return 0
        quota, _ = StorageQuota.objects.get_or_create(owner_id=self.owner_id)
        return (quota.total_used_bytes / self.quota_bytes) * 100

    @property
    def is_over_soft_limit(self):
        """Check if user is over the 90% soft limit"""
        return self.usage_percent >= 90

    @property
    def is_over_hard_limit(self):
        """Check if user is at or over the 100% hard limit"""
        return self.usage_percent >= 100


class ScheduledMechanismRun(models.Model):
    """Durable liveness evidence for recurring Drive mechanisms."""

    mechanism = models.CharField(max_length=100, unique=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_succeeded_at = models.DateTimeField(null=True, blank=True)
    last_failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.mechanism}: {self.last_succeeded_at or 'never succeeded'}"


class Activity(models.Model):
    """
    Activity log for files and folders.
    """
    EVENT_CREATE = 'create'
    EVENT_MOVE = 'move'
    EVENT_DELETE = 'delete'
    EVENT_SHARE = 'share'
    EVENT_DOWNLOAD = 'download'
    EVENT_RESTORE = 'restore'
    EVENT_CHOICES = [
        (EVENT_CREATE, 'Create'),
        ('upload', 'Upload'),
        ('update', 'Update'),
        ('rename', 'Rename'),
        (EVENT_MOVE, 'Move'),
        ('copy', 'Copy'),
        (EVENT_DELETE, 'Delete'),
        (EVENT_SHARE, 'Share'),
        ('unshare', 'Unshare'),
        ('trash', 'Trash'),
        (EVENT_DOWNLOAD, 'Download'),
        (EVENT_RESTORE, 'Restore'),
        ('view', 'View'),
    ]

    RESOURCE_FILE = 'file'
    RESOURCE_FOLDER = 'folder'
    RESOURCE_CHOICES = [
        (RESOURCE_FILE, 'File'),
        (RESOURCE_FOLDER, 'Folder'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Actor
    user_id = models.CharField(max_length=64, help_text="User ID who performed the action")
    actor_id = models.CharField(max_length=64, blank=True, db_index=True, help_text="Canonical actor/user ID (DRV-36)")

    # Target
    file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    folder = models.ForeignKey(Folder, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    resource_id = models.UUIDField(null=True, blank=True, db_index=True, help_text="Canonical file/folder UUID (DRV-36)")
    resource_type = models.CharField(max_length=16, choices=RESOURCE_CHOICES, blank=True, db_index=True)
    resource_name = models.TextField(blank=True)
    parent_id = models.UUIDField(null=True, blank=True)

    # Action
    ACTION_CHOICES = [
        ('create', 'Created'),
        ('upload', 'Uploaded'),
        ('update', 'Updated'),
        ('rename', 'Renamed'),
        ('move', 'Moved'),
        ('copy', 'Copied'),
        ('download', 'Downloaded'),
        ('share', 'Shared'),
        ('unshare', 'Unshared'),
        ('trash', 'Moved to Trash'),
        ('restore', 'Restored'),
        ('delete', 'Deleted'),
        ('view', 'Viewed'),
    ]
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES, blank=True, db_index=True)

    # Details
    details = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    objects = ActivityQuerySet.as_manager()

    class Meta:
        ordering = ['-timestamp']
        verbose_name_plural = "Activities"
        indexes = [
            models.Index(fields=['user_id', '-created_at']),
            models.Index(fields=['file', '-created_at']),
            models.Index(fields=['folder', '-created_at']),
            models.Index(fields=['resource_id', '-timestamp']),
            models.Index(fields=['actor_id', '-timestamp']),
        ]

    def __str__(self):
        target = self.file.name if self.file else (self.folder.name if self.folder else 'Unknown')
        return f"{self.action}: {target}"

    def save(self, *args, **kwargs):
        self._populate_canonical_fields()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {
                'actor_id', 'event_type', 'metadata',
                'resource_id', 'resource_type', 'resource_name', 'parent_id',
            }
        super().save(*args, **kwargs)

    def _populate_canonical_fields(self):
        if not self.actor_id and self.user_id:
            self.actor_id = str(self.user_id)
        if not self.event_type and self.action:
            self.event_type = self.action
        if not self.metadata and self.details:
            self.metadata = self.details
        if self.file_id:
            self.resource_id = self.resource_id or self.file_id
            self.resource_type = self.resource_type or self.RESOURCE_FILE
            if not self.resource_name and self.file:
                self.resource_name = self.file.name
            if not self.parent_id and self.file and self.file.folder_id:
                self.parent_id = self.file.folder_id
        elif self.folder_id:
            self.resource_id = self.resource_id or self.folder_id
            self.resource_type = self.resource_type or self.RESOURCE_FOLDER
            if not self.resource_name and self.folder:
                self.resource_name = self.folder.name
            if not self.parent_id and self.folder and self.folder.parent_id:
                self.parent_id = self.folder.parent_id

    @classmethod
    def record_event(
        cls,
        *,
        event_type,
        actor_id,
        resource,
        metadata=None,
        timestamp=None,
    ):
        """Insert one canonical activity/audit event."""
        kwargs = {
            'user_id': str(actor_id),
            'actor_id': str(actor_id),
            'action': event_type,
            'event_type': event_type,
            'metadata': metadata or {},
            'details': metadata or {},
            'timestamp': timestamp or timezone.now(),
        }
        if isinstance(resource, File):
            kwargs.update({
                'file': resource,
                'resource_id': resource.id,
                'resource_type': cls.RESOURCE_FILE,
                'resource_name': resource.name,
                'parent_id': resource.folder_id,
            })
        elif isinstance(resource, Folder):
            kwargs.update({
                'folder': resource,
                'resource_id': resource.id,
                'resource_type': cls.RESOURCE_FOLDER,
                'resource_name': resource.name,
                'parent_id': resource.parent_id,
            })
        else:
            raise TypeError("resource must be a File or Folder")
        return cls.objects.create(**kwargs)


class FileActivity(models.Model):
    """Per-user access activity backing Drive Recent (DRV-4 Slice 6).

    This is intentionally separate from the canonical Activity audit feed:
    Activity is append-only audit/provenance; FileActivity is user-facing
    access recency that is throttled/collapsed and ACL-filtered at read time.
    Rows survive share revoke/expiry and are hidden by the visibility predicate.
    """
    ACTION_OPEN = 'open'
    ACTION_PREVIEW = 'preview'
    ACTION_DOWNLOAD = 'download'
    ACTION_EDIT = 'edit'
    ACTION_CHOICES = [
        (ACTION_OPEN, 'Open'),
        (ACTION_PREVIEW, 'Preview'),
        (ACTION_DOWNLOAD, 'Download'),
        (ACTION_EDIT, 'Edit'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(max_length=64, db_index=True, help_text="User ID whose Recent feed owns this row")
    file = models.ForeignKey(File, on_delete=models.CASCADE, null=True, blank=True, related_name='recent_activities')
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, null=True, blank=True, related_name='recent_activities')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'File activities'
        indexes = [
            models.Index(fields=['user_id', '-created_at']),
            models.Index(fields=['file', '-created_at']),
            models.Index(fields=['folder', '-created_at']),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(file__isnull=False) & models.Q(folder__isnull=True))
                    | (models.Q(file__isnull=True) & models.Q(folder__isnull=False))
                ),
                name='drive_fileactivity_exactly_one_target',
            ),
        ]

    def __str__(self):
        target = self.file_id or self.folder_id or 'unknown'
        return f"{self.user_id} {self.action} {target} @ {self.created_at}"

    @classmethod
    def record(cls, *, user_id, action, file=None, folder=None, at=None, collapse_window=None):
        """Record access activity, collapsing repeated rows in a short window.

        The default collapse window is 10 minutes (DRV-24 §13). Repeated
        open/preview/download/edit events for the same caller+target+action
        update the existing row timestamp instead of inserting another row.
        """
        if bool(file) == bool(folder):
            raise ValueError('exactly one of file or folder is required')
        if action not in {choice for choice, _label in cls.ACTION_CHOICES}:
            raise ValueError(f'unsupported FileActivity action: {action}')
        now = at or timezone.now()
        seconds = (
            collapse_window
            if collapse_window is not None
            else getattr(settings, 'DRIVE_FILE_ACTIVITY_COLLAPSE_SECONDS', 10 * 60)
        )
        qs = cls.objects.filter(user_id=str(user_id), action=action)
        if file is not None:
            qs = qs.filter(file=file)
        else:
            qs = qs.filter(folder=folder)
        if seconds and seconds > 0:
            existing = qs.filter(created_at__gte=now - timedelta(seconds=seconds)).order_by('-created_at').first()
            if existing:
                existing.created_at = now
                existing.save(update_fields=['created_at'])
                return existing, False
        kwargs = {
            'user_id': str(user_id),
            'action': action,
            'created_at': now,
        }
        if file is not None:
            kwargs['file'] = file
        else:
            kwargs['folder'] = folder
        return cls.objects.create(**kwargs), True


class ShareAccessLog(models.Model):
    """Append-only audit trail for share accesses (DRV-28 / HLD §4.4 / INV-5).

    One row per access event regardless of outcome. Never updated or deleted in
    normal operation; survives share revocation and purge via on_delete=SET_NULL +
    denormalized share_id_copy (INV-5: revocation/purge must never destroy audit).
    Retention >= 1 year enforced at the purge layer (tasks.purge_dead_shares).
    """
    ACTION_VIEW = 'view'
    ACTION_DOWNLOAD = 'download'
    ACTION_PASSWORD_FAIL = 'password_fail'
    ACTION_DENIED = 'denied'
    ACTION_CHOICES = [
        (ACTION_VIEW, 'View'),
        (ACTION_DOWNLOAD, 'Download'),
        (ACTION_PASSWORD_FAIL, 'Password Fail'),
        (ACTION_DENIED, 'Denied'),
    ]

    RESULT_OK = 'ok'
    RESULT_DENIED = 'denied'
    RESULT_EXPIRED = 'expired'
    RESULT_BAD_PASSWORD = 'bad_password'
    RESULT_CHOICES = [
        (RESULT_OK, 'OK'),
        (RESULT_DENIED, 'Denied'),
        (RESULT_EXPIRED, 'Expired'),
        (RESULT_BAD_PASSWORD, 'Bad Password'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # SET_NULL so purging the Share row never cascades to audit (INV-5).
    share = models.ForeignKey(
        Share, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='access_logs',
    )
    # Denormalized copy so access logs remain queryable after share purge (INV-5).
    share_id_copy = models.UUIDField(db_index=True)

    accessed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Null for anonymous link access; set for authenticated callers.
    viewer_id = models.CharField(max_length=64, null=True, blank=True)
    viewer_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)

    class Meta:
        ordering = ['-accessed_at']
        verbose_name = 'Share Access Log'
        verbose_name_plural = 'Share Access Logs'
        indexes = [
            models.Index(fields=['share', '-accessed_at']),
        ]

    def __str__(self):
        return f"ShareAccessLog {self.share_id_copy} {self.action}/{self.result} @{self.accessed_at}"


# =============================================================================
# DRV-22 (VEN-49) — free-validation waitlist / early-access capture
# =============================================================================
class WaitlistEntry(models.Model):
    """Public /drive landing-page waitlist for the FREE validation track (DRV-22).

    Records early-access interest so we can validate Shizuha Drive demand with
    free external users — NO payment, no checkout (operator scope correction
    VEN-49, 2026-06-29). Persisted here so agents can inspect/export submissions.
    One row per email (case-insensitive); re-submitting updates, never duplicates.
    """
    STATUS_PENDING = 'pending'
    STATUS_INVITED = 'invited'
    STATUS_CONVERTED = 'converted'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_INVITED, 'Invited'),
        (STATUS_CONVERTED, 'Converted'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(db_index=True, help_text="Submitter email (waitlist key, stored lower-cased).")
    name = models.CharField(max_length=200, blank=True, default='')
    interest = models.TextField(blank=True, default='', help_text="Free-text: what they want to use Drive for.")
    source = models.CharField(max_length=64, blank=True, default='drive_landing',
                              help_text="Which surface/CTA captured this (analytics).")
    user_agent = models.CharField(max_length=400, blank=True, default='')
    referrer = models.CharField(max_length=400, blank=True, default='')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'drive_waitlist_entry'
        ordering = ['-created_at']
        verbose_name_plural = 'Waitlist entries'
        constraints = [
            # One waitlist row per email, case-insensitive (idempotent submit).
            models.UniqueConstraint(Lower('email'), name='uq_waitlist_email_ci'),
        ]

    def __str__(self):
        return f"{self.email} ({self.status})"


# =============================================================================
# VEN-153 — data export and offboarding (deletion request + grace period)
# =============================================================================
class DeletionRequest(models.Model):
    """User-initiated account offboarding with a 30-day grace period (VEN-153).

    When a user requests deletion, a DeletionRequest is created and the user's
    data enters a 30-day grace period. During this window the user can cancel
    the request. After expiry, a background task permanently deletes all files
    and marks the account as offboarded.

    Compliance notes:
    - Data is India-hosted (MinIO in the same region).
    - GDPR right-to-erasure: the 30-day grace gives users time to export data
      or change their mind; after expiry, deletion is irreversible.
    - A DeletionRequest record is retained as a non-identifying audit trail;
      owner_id, email, and reason are cleared before completion.
    """
    STATUS_PENDING = 'pending'
    STATUS_CANCELLED = 'cancelled'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.CharField(max_length=64, db_index=True, help_text="User ID from Shizuha ID")
    email = models.EmailField(blank=True, default='', help_text="User email at time of request (audit).")
    reason = models.TextField(blank=True, default='', help_text="Optional user-provided reason for leaving.")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    grace_expires_at = models.DateTimeField(help_text="When the grace period ends and deletion executes.")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'drive_deletion_request'
        ordering = ['-requested_at']
        verbose_name_plural = 'Deletion requests'
        constraints = [
            models.UniqueConstraint(
                fields=['owner_id'],
                condition=models.Q(status='pending'),
                name='uq_drive_pending_deletion_owner',
            ),
        ]
        indexes = [
            models.Index(fields=['owner_id', 'status']),
            models.Index(fields=['grace_expires_at', 'status']),
        ]

    def __str__(self):
        return f"DeletionRequest({self.owner_id}, {self.status})"

    def cancel(self):
        """Cancel during grace using a compare-and-set safe for stale instances."""
        now = timezone.now()
        updated = type(self).objects.filter(
            pk=self.pk,
            status=self.STATUS_PENDING,
            grace_expires_at__gt=now,
        ).update(
            status=self.STATUS_CANCELLED,
            cancelled_at=now,
            updated_at=now,
        )
        if not updated:
            self.refresh_from_db(fields=['status', 'grace_expires_at'])
            if self.status != self.STATUS_PENDING:
                raise ValidationError(f'Cannot cancel a {self.status} deletion request.')
            raise ValidationError('Cannot cancel after the deletion grace period has expired.')
        self.status = self.STATUS_CANCELLED
        self.cancelled_at = now
        self.updated_at = now

    def complete(self):
        """Mark the deletion request as completed after data purge."""
        self.status = self.STATUS_COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'completed_at', 'updated_at'])

    @property
    def is_expired(self):
        """Whether the grace period has passed and deletion should execute."""
        return self.status == self.STATUS_PENDING and self.grace_expires_at <= timezone.now()


class DeletionProcessorState(models.Model):
    """Singleton, durable liveness and fail-loud state for offboarding deletion."""

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK, editable=False)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_probe_at = models.DateTimeField(null=True, blank=True)
    last_alert_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    last_alert_status = models.CharField(max_length=128, blank=True, default='')
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_processed_count = models.PositiveIntegerField(default=0)
    last_failed_count = models.PositiveIntegerField(default=0)
    expired_pending_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'drive_deletion_processor_state'

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)


class Change(models.Model):
    """Ordered mutation record for the Drive change-feed (DRV-105).

    The BigAutoField ``id`` is the monotonic, opaque cursor: a change-feed
    consumer requests ``changes?cursor=<id>`` and receives every mutation with
    ``id > cursor`` in ascending order. Deletes are recorded as tombstones
    (payload carries the last-known resource state) and garbage-collected after
    ``expires_at`` so sync clients converge and GC locally.
    """

    MUTATION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('rename', 'Rename'),
        ('move', 'Move'),
        ('trash', 'Trash'),
        ('restore', 'Restore'),
        ('delete', 'Delete'),
    ]
    RESOURCE_FILE = 'file'
    RESOURCE_FOLDER = 'folder'
    RESOURCE_CHOICES = [
        (RESOURCE_FILE, 'File'),
        (RESOURCE_FOLDER, 'Folder'),
    ]

    id = models.BigAutoField(primary_key=True)  # monotonic cursor
    owner_id = models.CharField(max_length=64, db_index=True)
    resource_type = models.CharField(max_length=16, choices=RESOURCE_CHOICES)
    resource_id = models.UUIDField(db_index=True)
    mutation = models.CharField(max_length=16, choices=MUTATION_CHOICES)
    generation = models.PositiveBigIntegerField(
        default=1,
        help_text='Resource version/etag at this mutation',
    )
    parent_id = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text='Snapshot of resource state at this mutation',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Tombstone GC time (delete mutations)',
    )

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['owner_id', 'id']),
        ]

    def __str__(self):
        return f'{self.mutation} {self.resource_type}:{self.resource_id} @{self.id}'


class ConflictArtifact(models.Model):
    """Idempotent conflict-artifact record (DRV-105 keep-both).

    When a conditional write fails with 412, the sync engine creates a
    conflict copy. The server keys the artifact by a stable conflict identity
    supplied by the client (device_id + journal_op_id + observed_remote_generation,
    or a persisted conflict UUID) plus a client idempotency key, so a replay or
    retry returns the existing artifact instead of creating a duplicate copy.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.CharField(max_length=64, db_index=True)
    resource_id = models.UUIDField(db_index=True)
    conflict_identity = models.CharField(
        max_length=255,
        db_index=True,
        help_text='Stable conflict identity supplied by the client',
    )
    idempotency_key = models.CharField(max_length=255, db_index=True)
    name = models.CharField(
        max_length=255,
        help_text="Display name, e.g. 'report (conflicted copy 2026-08-16 10:00 UTC)'",
    )
    first_detected_at = models.DateTimeField(
        default=timezone.now,
        help_text='First-detected UTC timestamp, stored once',
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['owner_id', 'conflict_identity', 'idempotency_key']]
        indexes = [
            models.Index(fields=['owner_id', 'resource_id']),
        ]

    def __str__(self):
        return f'Conflict {self.name} ({self.owner_id})'


# PLAT-9234: the metering outbox model lives in drive/metering.py; importing
# it here registers it with the app registry (Django auto-discovers models
# only from this module) so the autodetector sees it and no drift migration
# is generated.
from .metering import OccurrenceOutbox  # noqa: E402,F401
