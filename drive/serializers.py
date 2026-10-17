"""
Serializers for Shizuha Drive API
"""
import hashlib
import os
import re
import zipfile

import magic
from rest_framework import serializers
from rest_framework.exceptions import APIException
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .models import (
    Folder, File, FileVersion, Thumbnail, Share, StorageQuota, Activity,
    ServiceStorageUsage, StorageTier, UserTier,
    FileType, ShareType, SharePermission, ShareAccessLog, ResumableUpload,
    WaitlistEntry,  # DRV-22
    DeletionRequest,  # VEN-153
)
from .permissions import require_share_access


_SHA256_HEX_RE = re.compile(r'^[0-9a-f]{64}$')
# Java ObjectOutputStream stream header (used by the income-tax e-filing portal
# when it downloads ITR / acknowledgement "PDF" files).
_JAVA_SERIAL_MAGIC = b'\xac\xed\x00\x05'
_PDF_MAGIC = b'%PDF'
# Compound File Binary Format (OLE2). Legacy Excel ``.xls`` workbooks use
# this container; libmagic labels the same valid bytes differently across
# distributions (for example ``application/vnd.ms-excel`` on the host and
# ``application/x-ole-storage`` in the production image).
_OLE2_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_TEXT_SUFFIXES = ('.csv', '.ini', '.json', '.md', '.txt')


def _extract_embedded_pdf(payload: bytes) -> bytes | None:
    """Return a pure PDF payload if ``payload`` is a Java-serialized PDF wrapper.

    Indian income-tax portal downloads often arrive as Java ObjectOutputStream
    blobs (magic ``aced 0005``) that *embed* a real ``%PDF…%%EOF`` document.
    libmagic reports these as ``application/octet-stream``, which Drive rejects.
    Extracting the embedded PDF lets agents store and open a real PDF.
    """
    if not payload.startswith(_JAVA_SERIAL_MAGIC):
        return None
    start = payload.find(_PDF_MAGIC)
    if start < 0:
        return None
    end = payload.rfind(b'%%EOF')
    if end < start:
        return None
    end += len(b'%%EOF')
    # Preserve a single trailing newline if the portal included one.
    if end < len(payload) and payload[end : end + 1] in (b'\n', b'\r'):
        end += 1
        if (
            end < len(payload)
            and payload[end - 1 : end + 1] == b'\r\n'
        ):
            end += 1
    pdf = payload[start:end]
    if not pdf.startswith(_PDF_MAGIC) or b'%%EOF' not in pdf[-16:]:
        return None
    return pdf


def _detect_upload_mime(uploaded_file):
    """Sniff uploads without rejecting known-good structured octet streams.

    libmagic can report ``application/octet-stream`` for:

    * Python-generated ZIP report packs (leading-chunk sniff only)
    * Income-tax portal ITR/ack downloads wrapped in Java serialization

    Drive permits ZIP and PDF.  Verify ZIP structure or an embedded ``%PDF``
    payload before applying the narrow fallbacks; arbitrary octet streams
    remain rejected.
    """
    head = uploaded_file.read(8192)
    uploaded_file.seek(0)
    detected = magic.from_buffer(head, mime=True)

    # Normalize only a filename-consistent legacy Excel container. Adding the
    # generic OLE MIME to ALLOWED_FILE_TYPES would accept any renamed Compound
    # File Binary Format payload; requiring both the binary signature and the
    # .xls suffix keeps the exception narrow and portable across libmagic
    # versions.
    filename = str(getattr(uploaded_file, 'name', '') or '').lower()
    if head.startswith(_OLE2_MAGIC) and filename.endswith('.xls'):
        return 'application/vnd.ms-excel'
    if head.startswith(_OLE2_MAGIC) and filename.endswith('.xlsx'):
        # Password-protected OOXML workbooks are stored in an OLE2 encrypted
        # package rather than the usual ZIP container. They remain Excel
        # workbooks and are opened by Excel using the .xlsx extension.
        return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    # libmagic uses format-specific MIME names for harmless text metadata and
    # ``application/x-empty`` for zero-byte text markers. Drive's allowlist
    # intentionally exposes those as text/plain, but only when the filename is
    # text-like and the payload is genuinely UTF-8 text.
    if filename.endswith(_TEXT_SUFFIXES):
        if not head:
            return 'text/plain'
        if b'\x00' not in head:
            try:
                head.decode('utf-8')
            except UnicodeDecodeError:
                pass
            else:
                if detected in {
                    'application/x-empty',
                    'application/x-wine-extension-ini',
                }:
                    return 'text/plain'

    if detected != 'application/octet-stream' and not head.startswith(
        _JAVA_SERIAL_MAGIC
    ):
        return detected

    # Prefer a real ZIP if the full stream is a valid archive.
    try:
        if zipfile.is_zipfile(uploaded_file):
            return 'application/zip'
    finally:
        uploaded_file.seek(0)

    # Java-wrapped PDF (income-tax portal). ``%PDF`` is near the start of every
    # sample we have (offsets ~247–505), so the 8 KiB head is sufficient.
    if head.startswith(_JAVA_SERIAL_MAGIC) and _PDF_MAGIC in head:
        return 'application/pdf'

    # Rare: wrapper larger than 8 KiB before the PDF marker — scan a bit more.
    if head.startswith(_JAVA_SERIAL_MAGIC):
        probe = uploaded_file.read(256 * 1024)
        uploaded_file.seek(0)
        if _PDF_MAGIC in probe:
            return 'application/pdf'

    return detected


class FolderNotWritable(serializers.ValidationError):
    """Safe, stable upload error for a hidden or non-editable folder.

    Drive deliberately does not distinguish a missing folder from one the
    caller cannot edit.  The structured code is nevertheless specific enough
    for service callers to fail before preparing a large upload.
    """

    default_code = 'folder_not_found_or_not_editable'
    default_detail = 'Folder was not found or is not editable by this caller.'

    def __init__(self):
        super().__init__(self.default_detail, code=self.default_code)


def _validate_writable_folder(user, folder_id):
    """Resolve an upload folder without disclosing inaccessible folder IDs."""
    if not folder_id:
        return None
    folder = Folder.objects.filter(id=folder_id, is_trashed=False).first()
    if not folder or not require_share_access(user, folder, SharePermission.EDIT):
        raise FolderNotWritable()
    return folder


class ChecksumMismatch(APIException):
    status_code = 409
    default_code = 'checksum_mismatch'
    default_detail = 'Uploaded bytes did not match expected SHA-256'


class UploadReadFailed(APIException):
    status_code = 400
    default_code = 'upload_read_failed'
    default_detail = 'Unable to read uploaded file stream'


class StorageQuotaExceeded(APIException):
    status_code = 413
    default_code = 'quota_exceeded'
    default_detail = 'This upload would exceed your storage quota.'


class DaysUntilPurgeMixin(serializers.Serializer):
    """DRV-7: expose `days_until_purge` on a trashed item (null when not
    trashed). Computed from trashed_at + DRIVE_TRASH_RETENTION_DAYS.
    (Subclasses serializers.Serializer so DRF's metaclass collects the field.)"""
    days_until_purge = serializers.SerializerMethodField()

    def get_days_until_purge(self, obj):
        if not getattr(obj, 'is_trashed', False) or not getattr(obj, 'trashed_at', None):
            return None
        import math
        from django.utils import timezone
        purge_at = getattr(obj, 'restore_until', None)
        if not purge_at:
            from datetime import timedelta
            days = getattr(settings, 'DRIVE_TRASH_RETENTION_DAYS', 30)
            purge_at = obj.trashed_at + timedelta(days=days)
        # Round UP partial days so an item trashed 10d ago with a 30d window
        # reads "20 days until purge", not 19 (sub-second truncation).
        remaining = math.ceil((purge_at - timezone.now()).total_seconds() / 86400)
        return max(0, remaining)


class FolderSerializer(DaysUntilPurgeMixin, serializers.ModelSerializer):
    """Serializer for folders"""
    path = serializers.ReadOnlyField()
    file_count = serializers.SerializerMethodField()
    folder_count = serializers.SerializerMethodField()
    total_size = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = [
            'id', 'name', 'parent', 'color', 'description',
            'is_starred', 'is_trashed', 'trashed_at', 'days_until_purge',
            'path', 'file_count', 'folder_count', 'total_size',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'owner_id', 'is_trashed', 'trashed_at', 'created_at', 'updated_at']

    def get_file_count(self, obj):
        return obj.files.filter(is_trashed=False).count()

    def get_folder_count(self, obj):
        return obj.children.filter(is_trashed=False).count()

    def get_total_size(self, obj):
        return obj.files.filter(is_trashed=False).aggregate(
            total=models.Sum('size')
        )['total'] or 0

    def create(self, validated_data):
        user = self.context['request'].user
        validated_data['owner_id'] = user.id
        return super().create(validated_data)


class FolderListSerializer(DaysUntilPurgeMixin, serializers.ModelSerializer):
    """Lightweight serializer for folder lists"""

    class Meta:
        model = Folder
        fields = ['id', 'name', 'parent', 'color', 'is_starred', 'updated_at', 'days_until_purge']


class FolderTreeSerializer(serializers.ModelSerializer):
    """Serializer for folder tree view"""
    children = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = ['id', 'name', 'color', 'children']

    def get_children(self, obj):
        children = obj.children.filter(is_trashed=False)
        return FolderTreeSerializer(children, many=True).data


class ThumbnailSerializer(serializers.ModelSerializer):
    """Serializer for thumbnails"""
    url = serializers.SerializerMethodField()

    class Meta:
        model = Thumbnail
        fields = ['size', 'width', 'height', 'url']

    def get_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class FileSerializer(DaysUntilPurgeMixin, serializers.ModelSerializer):
    """Full serializer for files"""
    path = serializers.ReadOnlyField()
    size_human = serializers.ReadOnlyField()
    thumbnails = ThumbnailSerializer(many=True, read_only=True)
    folder_name = serializers.CharField(source='folder.name', read_only=True)
    # DRV-25 P1: client must echo the current version on any clobber-capable mutation.
    # Write-only; not returned in responses (current version is in `version`).
    base_version = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = File
        fields = [
            'id', 'name', 'folder', 'folder_name', 'path',
            'file_type', 'mime_type', 'size', 'size_human', 'extension',
            'checksum', 'checksum_algorithm',
            'width', 'height', 'duration',
            'has_thumbnail', 'thumbnails',
            'is_starred', 'is_trashed', 'trashed_at', 'deleted_at', 'deleted_by', 'restore_until', 'days_until_purge',
            'version', 'is_processed',
            'created_at', 'updated_at',
            'base_version',
        ]
        read_only_fields = [
            'id', 'owner_id', 'file_type', 'mime_type', 'size', 'extension',
            'checksum', 'checksum_algorithm',
            'width', 'height', 'duration', 'has_thumbnail',
            'is_trashed', 'trashed_at', 'deleted_at', 'deleted_by', 'restore_until', 'version', 'is_processed',
            'created_at', 'updated_at'
        ]


class FileListSerializer(DaysUntilPurgeMixin, serializers.ModelSerializer):
    """Lightweight serializer for file lists"""
    size_human = serializers.ReadOnlyField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = File
        fields = [
            'id', 'name', 'folder', 'file_type', 'mime_type',
            'size', 'size_human', 'extension',
            'checksum', 'checksum_algorithm',
            'has_thumbnail', 'thumbnail_url',
            'is_starred', 'is_trashed', 'deleted_at', 'deleted_by', 'restore_until',
            'version', 'updated_at', 'days_until_purge'
        ]

    def get_thumbnail_url(self, obj):
        if not obj.has_thumbnail:
            return None
        thumbnail = obj.thumbnails.filter(size='small').first()
        if thumbnail and thumbnail.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(thumbnail.image.url)
        return None


class FileUploadSerializer(serializers.Serializer):
    """Serializer for file uploads"""
    # Empty text markers are meaningful source evidence (for example a
    # month-specific "No Trade" note). MIME validation below still limits
    # zero-byte uploads to text-like filename suffixes.
    file = serializers.FileField(allow_empty_file=True)
    folder = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(required=False, max_length=255)
    expected_sha256 = serializers.CharField(required=False, write_only=True, allow_blank=False, max_length=64)

    def validate(self, attrs):
        expected = self._expected_sha256(attrs)
        if expected is not None and not _SHA256_HEX_RE.match(expected):
            raise serializers.ValidationError({
                'error': {
                    'code': 'invalid_checksum_format',
                    'message': 'expected_sha256 must be a 64-character lowercase SHA-256 hex digest.',
                }
            })
        if expected is not None:
            attrs['expected_sha256'] = expected
        return attrs

    def validate_file(self, value):
        # Check file size
        if value.size > settings.MAX_UPLOAD_SIZE:
            raise serializers.ValidationError(
                f"File too large. Maximum size is {settings.MAX_UPLOAD_SIZE / 1024 / 1024}MB"
            )

        # Check MIME type
        mime = _detect_upload_mime(value)

        if mime not in settings.ALLOWED_FILE_TYPES:
            raise serializers.ValidationError(
                f"File type '{mime}' is not allowed"
            )

        return value

    def validate_folder(self, value):
        if value:
            user = self.context['request'].user
            _validate_writable_folder(user, value)
        return value

    def create(self, validated_data):
        from django.core.files.base import ContentFile

        user = self.context['request'].user
        uploaded_file = validated_data['file']
        folder_id = validated_data.get('folder')
        name = validated_data.get('name') or uploaded_file.name
        expected_sha256 = self._expected_sha256(validated_data)

        # Detect MIME type
        mime_type = _detect_upload_mime(uploaded_file)

        # Determine file type category
        file_type = self._detect_file_type(mime_type)

        # Get extension
        extension = name.rsplit('.', 1)[-1].lower() if '.' in name else ''

        # Read full payload once so we can (a) honour client checksums of the
        # original bytes and (b) unwrap Java-serialized income-tax PDFs into a
        # real PDF before writing object storage.
        try:
            raw_bytes = b''.join(uploaded_file.chunks())
            uploaded_file.seek(0)
        except OSError as exc:
            raise UploadReadFailed({
                'error': {
                    'code': 'upload_read_failed',
                    'message': 'Unable to read uploaded file stream; upload was not committed.',
                    'retryable': True,
                    'details': {'reason': str(exc)},
                }
            }) from exc

        original_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if expected_sha256 and original_sha256 != expected_sha256:
            raise ChecksumMismatch({
                'error': {
                    'code': 'checksum_mismatch',
                    'message': 'Uploaded bytes did not match expected SHA-256',
                    'retryable': False,
                    'details': {
                        'expected_sha256': expected_sha256,
                        'actual_sha256': original_sha256,
                    },
                }
            })

        stored_bytes = raw_bytes
        unwrapped = _extract_embedded_pdf(raw_bytes)
        if unwrapped is not None and mime_type == 'application/pdf':
            stored_bytes = unwrapped
            if not name.lower().endswith('.pdf'):
                name = f'{name}.pdf'
                extension = 'pdf'

        md5_digest = hashlib.md5(stored_bytes, usedforsecurity=False).hexdigest()
        actual_sha256 = hashlib.sha256(stored_bytes).hexdigest()
        uploaded_file = ContentFile(stored_bytes, name=name)
        # Recompute extension after possible name normalization.
        extension = name.rsplit('.', 1)[-1].lower() if '.' in name else extension

        # Get or check folder
        folder = None
        if folder_id:
            folder = Folder.objects.get(id=folder_id)

        # Create the visible DB row only after checksum computation and
        # optional client-digest verification have succeeded. FileField writes
        # bytes before the DB insert; if the insert fails after storage write,
        # delete the just-written object so no zombie partial remains (DRV-32
        # atomic upload boundary).
        file_obj = File(
            name=name,
            owner_id=user.id,
            folder=folder,
            file=uploaded_file,
            file_type=file_type,
            mime_type=mime_type,
            size=len(stored_bytes),
            extension=extension,
            checksum_md5=md5_digest,
            checksum_sha256=actual_sha256,
            checksum=actual_sha256,
            checksum_algorithm='sha256',
        )
        try:
            with transaction.atomic():
                reservation, quota = StorageQuota.reserve_upload(user.id, uploaded_file.size)
                if reservation is None:
                    raise StorageQuotaExceeded({
                        'error': 'quota_exceeded',
                        'message': 'This upload would exceed your storage quota.',
                        'details': {
                            'requested_bytes': uploaded_file.size,
                            'used_bytes': quota.total_used_bytes,
                            'quota_bytes': quota.quota_bytes,
                            'available_bytes': quota.total_remaining_bytes,
                        },
                    })
                file_obj.save()
                reservation.mark_committed()
        except StorageQuotaExceeded:
            raise
        except Exception:
            if getattr(file_obj, 'file', None) and file_obj.file.name:
                try:
                    file_obj.file.delete(save=False)
                except Exception:
                    pass
            raise

        return file_obj

    def _expected_sha256(self, attrs):
        expected = attrs.get('expected_sha256')
        request = self.context.get('request')
        if not expected and request is not None:
            expected = request.headers.get('X-Drive-Checksum-SHA256')
        return expected.strip() if isinstance(expected, str) else expected

    def to_representation(self, instance):
        return FileSerializer(instance, context=self.context).data

    def _detect_file_type(self, mime_type):
        """Detect file type category from MIME type"""
        if mime_type.startswith('image/'):
            return FileType.IMAGE
        elif mime_type.startswith('video/'):
            return FileType.VIDEO
        elif mime_type.startswith('audio/'):
            return FileType.AUDIO
        elif mime_type in ['application/pdf']:
            return FileType.DOCUMENT
        elif 'word' in mime_type or mime_type == 'text/plain':
            return FileType.DOCUMENT
        elif 'excel' in mime_type or 'spreadsheet' in mime_type or mime_type == 'text/csv':
            return FileType.SPREADSHEET
        elif 'powerpoint' in mime_type or 'presentation' in mime_type:
            return FileType.PRESENTATION
        elif 'zip' in mime_type or 'tar' in mime_type or 'rar' in mime_type or 'compressed' in mime_type:
            return FileType.ARCHIVE
        elif 'javascript' in mime_type or 'json' in mime_type or 'xml' in mime_type:
            return FileType.CODE
        return FileType.OTHER


class FileVersionSerializer(serializers.ModelSerializer):
    """Serializer for file versions"""
    size_human = serializers.SerializerMethodField()

    class Meta:
        model = FileVersion
        fields = [
            'id', 'version_number', 'size', 'size_human',
            'created_by_id', 'created_at', 'comment'
        ]

    def get_size_human(self, obj):
        size = obj.size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class ShareSerializer(serializers.ModelSerializer):
    """Serializer for shares"""
    share_url = serializers.SerializerMethodField()
    target_name = serializers.SerializerMethodField()
    target_type = serializers.SerializerMethodField()

    class Meta:
        model = Share
        fields = [
            'id', 'file', 'folder',
            'share_type', 'permission',
            'link_token', 'shared_with_id', 'shared_with_email',
            'organization_id',
            'expires_at', 'allow_download', 'notify_on_access',
            'access_count', 'last_accessed_at',
            'is_active', 'share_url',
            'target_name', 'target_type',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'link_token', 'created_by_id',
            'access_count', 'last_accessed_at',
            'created_at', 'updated_at'
        ]

    def get_share_url(self, obj):
        if obj.share_type == ShareType.LINK and obj.link_token:
            host = (os.environ.get('SHIZUHA_PUBLIC_HOST') or 'shizuha.com').strip()
            scheme = (os.environ.get('SHIZUHA_PUBLIC_SCHEME') or 'https').strip()
            return f"{scheme}://{host}/drive/share/{obj.link_token}"
        return None

    def get_target_name(self, obj):
        if obj.file:
            return obj.file.name
        elif obj.folder:
            return obj.folder.name
        return None

    def get_target_type(self, obj):
        if obj.file:
            return 'file'
        elif obj.folder:
            return 'folder'
        return None

    def validate(self, data):
        # Must have either file or folder, not both
        if not data.get('file') and not data.get('folder'):
            raise serializers.ValidationError("Must specify either file or folder to share")
        if data.get('file') and data.get('folder'):
            raise serializers.ValidationError("Cannot share both file and folder in same share")
        return data

    def create(self, validated_data):
        import secrets
        user = self.context['request'].user
        validated_data['created_by_id'] = user.id

        # Generate link token for link shares
        if validated_data.get('share_type') == ShareType.LINK:
            validated_data['link_token'] = secrets.token_urlsafe(32)

        return super().create(validated_data)


class ShareCreateSerializer(serializers.Serializer):
    """Serializer for creating shares"""
    file = serializers.UUIDField(required=False)
    folder = serializers.UUIDField(required=False)
    share_type = serializers.ChoiceField(choices=ShareType.choices)
    permission = serializers.ChoiceField(choices=SharePermission.choices, default=SharePermission.VIEW)
    shared_with_email = serializers.EmailField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    allow_download = serializers.BooleanField(default=True)
    password = serializers.CharField(required=False, write_only=True)
    # DRV-14 INV-4: cross-org gate.  `organization_id` is NEVER accepted from the
    # client — it is derived server-side from the owner's trusted JWT claims.
    # `grantee_org_id` is the caller's hint about the grantee's org so we can
    # classify same-org vs cross-org at create time (user/org share types only).
    grantee_org_id = serializers.CharField(required=False, write_only=True, allow_blank=True)
    allow_cross_org = serializers.BooleanField(required=False, default=False, write_only=True)

    def validate(self, data):
        if not data.get('file') and not data.get('folder'):
            raise serializers.ValidationError("Must specify file or folder")
        if data.get('file') and data.get('folder'):
            raise serializers.ValidationError("Cannot share both file and folder")

        # Verify ownership
        user = self.context['request'].user
        if data.get('file'):
            try:
                File.objects.get(id=data['file'], owner_id=user.id)
            except File.DoesNotExist:
                raise serializers.ValidationError("File not found")
        if data.get('folder'):
            try:
                Folder.objects.get(id=data['folder'], owner_id=user.id)
            except Folder.DoesNotExist:
                raise serializers.ValidationError("Folder not found")

        return data


class StorageQuotaSerializer(serializers.ModelSerializer):
    """Serializer for storage quota"""
    used_human = serializers.ReadOnlyField()
    quota_human = serializers.ReadOnlyField()
    usage_percent = serializers.ReadOnlyField()
    remaining_bytes = serializers.ReadOnlyField()
    total_used_bytes = serializers.ReadOnlyField()
    total_used_human = serializers.ReadOnlyField()
    total_usage_percent = serializers.ReadOnlyField()
    total_remaining_bytes = serializers.ReadOnlyField()
    active_reserved_bytes = serializers.ReadOnlyField()
    by_service = serializers.SerializerMethodField()

    class Meta:
        model = StorageQuota
        fields = [
            'quota_bytes', 'used_bytes', 'used_human', 'quota_human',
            'usage_percent', 'remaining_bytes',
            'total_used_bytes', 'total_used_human', 'total_usage_percent',
            'active_reserved_bytes', 'total_remaining_bytes',
            'file_count', 'folder_count',
            'by_service',
            'updated_at'
        ]
        read_only_fields = fields

    def get_by_service(self, obj):
        services = ServiceStorageUsage.objects.filter(owner_id=obj.owner_id)
        result = {'drive': {'used_bytes': obj.used_bytes, 'file_count': obj.file_count}}
        for svc in services:
            result[svc.service] = {
                'used_bytes': svc.used_bytes,
                'file_count': svc.file_count,
            }
        return result


class ActivitySerializer(serializers.ModelSerializer):
    """Serializer for the Drive activity feed.

    DRV-37 exposes the canonical DRV-36 audit fields while keeping the legacy
    target/action fields additive for older dashboard clients.
    """
    target_name = serializers.SerializerMethodField()
    target_type = serializers.SerializerMethodField()

    class Meta:
        model = Activity
        fields = [
            'id',
            'event_type', 'actor_id',
            'resource_id', 'resource_type', 'resource_name',
            'timestamp', 'metadata',
            # Legacy fields retained for compatibility with existing dashboard code.
            'action', 'target_name', 'target_type', 'details', 'created_at',
        ]

    def get_target_name(self, obj):
        if obj.resource_name:
            return obj.resource_name
        if obj.file:
            return obj.file.name
        elif obj.folder:
            return obj.folder.name
        return None

    def get_target_type(self, obj):
        if obj.resource_type:
            return obj.resource_type
        if obj.file:
            return 'file'
        elif obj.folder:
            return 'folder'
        return None


class ServiceStorageUsageSerializer(serializers.ModelSerializer):
    """Serializer for service storage usage"""
    class Meta:
        model = ServiceStorageUsage
        fields = ['service', 'used_bytes', 'file_count', 'updated_at']
        read_only_fields = ['updated_at']


class StorageReportSerializer(serializers.Serializer):
    """Serializer for incoming storage usage reports from services"""
    service = serializers.CharField(max_length=50)
    used_bytes = serializers.IntegerField(min_value=0)
    file_count = serializers.IntegerField(min_value=0)
    reservation_id = serializers.UUIDField(required=False)


class CanUploadSerializer(serializers.Serializer):
    """Serializer for can-upload check requests"""
    file_size = serializers.IntegerField(min_value=0)
    reservation_ttl_seconds = serializers.IntegerField(
        min_value=30,
        max_value=3600,
        required=False,
        default=900,
    )


class CanUploadResponseSerializer(serializers.Serializer):
    """Response serializer for can-upload check"""
    allowed = serializers.BooleanField()
    reservation_id = serializers.UUIDField(allow_null=True)
    reservation_expires_at = serializers.DateTimeField(allow_null=True)
    remaining_bytes = serializers.IntegerField()
    total_used = serializers.IntegerField()
    quota_bytes = serializers.IntegerField()


class ShareAccessLogSerializer(serializers.ModelSerializer):
    """Read-only serializer for the share access audit log (DRV-28 / HLD §4.4)."""

    class Meta:
        model = ShareAccessLog
        fields = [
            'id', 'share', 'share_id_copy',
            'accessed_at', 'viewer_id', 'viewer_ip', 'user_agent',
            'action', 'result',
        ]
        read_only_fields = fields


class StorageTierSerializer(serializers.ModelSerializer):
    """Serializer for storage tier definitions"""
    quota_human = serializers.ReadOnlyField()
    soft_limit_bytes = serializers.ReadOnlyField()

    class Meta:
        model = StorageTier
        fields = [
            'id', 'name', 'slug', 'quota_bytes', 'quota_human',
            'soft_limit_bytes', 'price_monthly', 'price_yearly',
            'description', 'is_active', 'sort_order',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserTierSerializer(serializers.ModelSerializer):
    """Serializer for user tier assignments"""
    tier_name = serializers.CharField(source='tier.name', read_only=True)
    tier_slug = serializers.CharField(source='tier.slug', read_only=True)
    soft_limit_bytes = serializers.ReadOnlyField()
    usage_percent = serializers.ReadOnlyField()
    is_over_soft_limit = serializers.ReadOnlyField()
    is_over_hard_limit = serializers.ReadOnlyField()
    pending_tier_name = serializers.CharField(source='pending_tier.name', read_only=True, default=None)
    pending_tier_slug = serializers.CharField(source='pending_tier.slug', read_only=True, default=None)

    class Meta:
        model = UserTier
        fields = [
            'id', 'tier', 'tier_name', 'tier_slug',
            'quota_bytes', 'soft_limit_bytes',
            'usage_percent', 'is_over_soft_limit', 'is_over_hard_limit',
            'pending_tier', 'pending_tier_name', 'pending_tier_slug',
            'pending_quota_bytes',
            'billing_cycle', 'next_billing_date',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserTierScheduleSerializer(serializers.Serializer):
    """Serializer for scheduling a tier change"""
    tier_id = serializers.UUIDField()
    billing_cycle = serializers.ChoiceField(choices=['monthly', 'yearly'], required=False)

    def validate_tier_id(self, value):
        try:
            tier = StorageTier.objects.get(id=value, is_active=True)
            return tier
        except StorageTier.DoesNotExist:
            raise serializers.ValidationError("Tier not found or inactive")


# Import models for Sum aggregation
from django.db import models

class ResumableUploadCreateSerializer(serializers.Serializer):
    """Create a resumable upload session without buffering file content."""
    name = serializers.CharField(max_length=255, required=False)
    filename = serializers.CharField(max_length=255, required=False)
    size = serializers.IntegerField(min_value=1, required=False)
    total_size = serializers.IntegerField(min_value=1, required=False)
    folder = serializers.UUIDField(required=False, allow_null=True)
    folder_id = serializers.UUIDField(required=False, allow_null=True)
    mime_type = serializers.CharField(required=False, allow_blank=True, max_length=100)
    checksum_sha256 = serializers.CharField(required=False, allow_blank=True, max_length=64)
    expected_sha256 = serializers.CharField(required=False, allow_blank=True, max_length=64)
    chunk_size = serializers.IntegerField(required=False, min_value=1024 * 1024, default=5 * 1024 * 1024)
    chunk_size_hint = serializers.IntegerField(required=False, min_value=1024 * 1024)

    def validate_folder(self, value):
        if value:
            user = self.context['request'].user
            _validate_writable_folder(user, value)
        return value

    def validate(self, attrs):
        attrs['name'] = attrs.get('name') or attrs.get('filename')
        attrs['size'] = attrs.get('size') or attrs.get('total_size')
        attrs['folder'] = attrs.get('folder') or attrs.get('folder_id')
        attrs['checksum_sha256'] = (attrs.get('checksum_sha256') or attrs.get('expected_sha256') or '').lower()
        attrs['chunk_size'] = attrs.get('chunk_size') or attrs.get('chunk_size_hint') or 5 * 1024 * 1024
        if not attrs.get('name'):
            raise serializers.ValidationError({'filename': 'This field is required.'})
        if not attrs.get('size'):
            raise serializers.ValidationError({'total_size': 'This field is required.'})
        if attrs['checksum_sha256'] and (
            len(attrs['checksum_sha256']) != 64
            or any(char not in '0123456789abcdef' for char in attrs['checksum_sha256'])
        ):
            raise serializers.ValidationError({'expected_sha256': 'Expected a 64-character lowercase SHA-256 hex digest.'})

        user = self.context['request'].user
        if attrs.get('folder'):
            try:
                _validate_writable_folder(user, attrs['folder'])
            except serializers.ValidationError:
                raise serializers.ValidationError({'folder_id': 'Folder not found.'})
        quota, _ = StorageQuota.objects.get_or_create(owner_id=user.id)
        reserved = ResumableUpload.objects.filter(
            owner_id=user.id,
            status=ResumableUpload.Status.ACTIVE,
            expires_at__gt=timezone.now(),
        ).aggregate(total=Sum('size'))['total'] or 0
        if quota.total_used_bytes + reserved + attrs['size'] > quota.quota_bytes:
            raise serializers.ValidationError("Storage quota exceeded")
        return attrs


class ResumableUploadSerializer(serializers.ModelSerializer):
    file_id = serializers.UUIDField(source='file.id', read_only=True, allow_null=True)
    session_id = serializers.UUIDField(source='id', read_only=True)
    filename = serializers.CharField(source='name', read_only=True)
    total_size = serializers.IntegerField(source='size', read_only=True)
    committed_offset = serializers.IntegerField(source='uploaded_bytes', read_only=True)
    expected_sha256 = serializers.CharField(source='checksum_sha256', read_only=True)
    chunk_size_hint = serializers.IntegerField(source='chunk_size', read_only=True)

    class Meta:
        model = ResumableUpload
        fields = [
            'id', 'name', 'folder', 'size', 'mime_type', 'chunk_size',
            'uploaded_bytes', 'checksum_sha256', 'status', 'file_id',
            'session_id', 'filename', 'total_size', 'committed_offset',
            'expected_sha256', 'chunk_size_hint',
            'created_at', 'updated_at', 'expires_at',
        ]
        read_only_fields = fields


class WaitlistEntrySerializer(serializers.ModelSerializer):
    """DRV-22: agent inspect/export view of waitlist submissions."""
    class Meta:
        model = WaitlistEntry
        fields = ['id', 'email', 'name', 'interest', 'source', 'status', 'created_at']
        read_only_fields = fields


# =============================================================================
# VEN-153 — data export and offboarding
# =============================================================================
class DeletionRequestSerializer(serializers.ModelSerializer):
    """VEN-153: view/cancel deletion requests."""
    days_remaining = serializers.SerializerMethodField()

    class Meta:
        model = DeletionRequest
        fields = [
            'id', 'owner_id', 'email', 'reason', 'status',
            'requested_at', 'grace_expires_at', 'days_remaining',
            'cancelled_at', 'completed_at',
        ]
        read_only_fields = [
            'id', 'owner_id', 'status', 'requested_at',
            'grace_expires_at', 'days_remaining', 'cancelled_at', 'completed_at',
        ]

    def get_days_remaining(self, obj):
        if obj.status != DeletionRequest.STATUS_PENDING:
            return 0
        remaining = obj.grace_expires_at - timezone.now()
        return max(0, remaining.days)


class DeletionRequestCreateSerializer(serializers.Serializer):
    """VEN-153: initiate offboarding."""
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class ExportRequestSerializer(serializers.Serializer):
    """VEN-153: request a data export (ZIP download of all files)."""
    pass  # No input fields needed; export is a simple trigger
