"""
Celery tasks for Shizuha Drive

Tasks for:
- File processing (thumbnails, metadata extraction)
- Trash cleanup (auto-delete after 30 days)
- Storage quota recalculation
"""
import logging
import os
from io import BytesIO
from pathlib import Path
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from datetime import timedelta

logger = logging.getLogger(__name__)

PENDING_TIER_MECHANISM = 'apply_pending_tier_changes'

CONTENT_TEXT_MAX_BYTES = 256 * 1024
_TRUNCATION_MARKER = b'\n[TRUNCATED]'
_TEXT_EXTENSIONS = {
    'txt', 'text', 'md', 'markdown', 'rst', 'csv', 'tsv', 'json', 'jsonl', 'xml',
    'html', 'htm', 'css', 'js', 'ts', 'tsx', 'jsx', 'py', 'rb', 'go', 'rs', 'java',
    'kt', 'kts', 'swift', 'c', 'h', 'cc', 'cpp', 'hpp', 'cs', 'php', 'sh', 'bash',
    'zsh', 'fish', 'sql', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'log',
}
_TEXT_MIME_PREFIXES = ('text/',)
_TEXT_MIME_TYPES = {
    'application/json', 'application/xml', 'application/javascript',
    'application/x-javascript', 'application/sql', 'application/x-yaml',
    'application/yaml', 'application/toml', 'application/x-ndjson',
}

# DRV-4 HLD §4 P1 scope also covers PDF and office documents. OOXML formats are
# zip archives parsed with the stdlib; PDF uses pypdf. Documents larger than
# this are skipped (content_text=None) so extraction can't blow worker memory.
DOCUMENT_SOURCE_MAX_BYTES = 25 * 1024 * 1024
# Zip-bomb guard for OOXML members: caps apply to the DECLARED uncompressed
# size (ZipInfo.file_size) before any decompression; Python's zipfile refuses
# streams that exceed the declared size, so these bounds hold at read time.
OOXML_MEMBER_MAX_BYTES = 8 * 1024 * 1024
OOXML_TOTAL_MAX_BYTES = 32 * 1024 * 1024
_PDF_MIME_TYPES = {'application/pdf'}
_PDF_EXTENSIONS = {'pdf'}
_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
_PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
_OOXML_MIME_TO_KIND = {_DOCX_MIME: 'docx', _XLSX_MIME: 'xlsx', _PPTX_MIME: 'pptx'}
_OOXML_EXTENSIONS = {'docx', 'xlsx', 'pptx'}


def _cap_bytes_with_marker(data: bytes, max_bytes: int = CONTENT_TEXT_MAX_BYTES) -> bytes:
    if len(data) <= max_bytes:
        return data
    if max_bytes <= len(_TRUNCATION_MARKER):
        return data[:max_bytes]
    return data[:max_bytes - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _decode_search_text(data: bytes) -> str:
    # Prefer UTF-8, but keep extraction fail-closed for mixed/legacy text files.
    return data.decode('utf-8', errors='replace')


def _file_extension(file_obj) -> str:
    return (file_obj.extension or os.path.splitext(file_obj.name or '')[1].lstrip('.')).lower()


def is_plain_text_indexable(file_obj) -> bool:
    mime_type = (file_obj.mime_type or '').lower()
    extension = _file_extension(file_obj)
    return (
        mime_type.startswith(_TEXT_MIME_PREFIXES)
        or mime_type in _TEXT_MIME_TYPES
        or extension in _TEXT_EXTENSIONS
    )


def _document_kind(file_obj) -> str | None:
    """Classify PDF/OOXML documents in P1 extraction scope, else None."""
    mime_type = (file_obj.mime_type or '').lower()
    extension = _file_extension(file_obj)
    if mime_type in _PDF_MIME_TYPES or extension in _PDF_EXTENSIONS:
        return 'pdf'
    if mime_type in _OOXML_MIME_TO_KIND:
        return _OOXML_MIME_TO_KIND[mime_type]
    if extension in _OOXML_EXTENSIONS:
        return extension
    return None


def _cap_text_with_marker(text: str) -> str:
    return _decode_search_text(_cap_bytes_with_marker(text.encode('utf-8')))


def _extract_pdf_text(data: bytes) -> str:
    """Page-by-page pypdf extraction, stopping once the content cap is reached."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    collected = 0
    for page in reader.pages:
        page_text = page.extract_text() or ''
        if page_text:
            parts.append(page_text)
            collected += len(page_text.encode('utf-8'))
        if collected > CONTENT_TEXT_MAX_BYTES:
            break
    return '\n'.join(parts)


def _extract_ooxml_text(data: bytes, kind: str) -> str:
    """Extract text nodes from OOXML zip members with the stdlib.

    docx/xlsx/pptx are zip archives; the searchable text lives in text
    elements (w:t / t / a:t) of specific members. Local-name matching on the
    element tag covers all three namespaces without extra dependencies.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    members_by_kind = {
        'docx': lambda names: [n for n in names if n == 'word/document.xml'],
        'xlsx': lambda names: [n for n in names if n == 'xl/sharedStrings.xml'],
        'pptx': lambda names: sorted(
            n for n in names
            if n.startswith('ppt/slides/slide') and n.endswith('.xml')
        ),
    }

    parts: list[str] = []
    collected = 0
    budget = OOXML_TOTAL_MAX_BYTES
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for member in members_by_kind[kind](archive.namelist()):
            # Zip-bomb guard: bound by the declared uncompressed size BEFORE
            # decompressing/parsing. zipfile enforces the declared size on
            # read, so a lying header cannot stream past these caps either.
            declared = archive.getinfo(member).file_size
            if declared > OOXML_MEMBER_MAX_BYTES:
                logger.warning(
                    "Skipping OOXML member %s: declared %d bytes exceeds cap",
                    member, declared,
                )
                continue
            if declared > budget:
                break
            budget -= declared
            with archive.open(member) as stream:
                for event, element in ET.iterparse(stream, events=('end',)):
                    if element.tag.endswith('}t') or element.tag == 't':
                        text = (element.text or '').strip()
                        if text:
                            parts.append(text)
                            collected += len(text.encode('utf-8'))
                            if collected > CONTENT_TEXT_MAX_BYTES:
                                return ' '.join(parts)
                    element.clear()
    return ' '.join(parts)


def extract_searchable_content_text(file_obj) -> str | None:
    """Extract capped text for DRV-4 search without logging file content.

    Covers plain text/code, PDF, and OOXML office documents (DRV-4 HLD §4 P1
    scope). Unsupported/binary formats return None so search safely degrades to
    name/metadata. The caller records extraction exceptions on the File row while
    still allowing upload processing to complete.
    """
    if is_plain_text_indexable(file_obj):
        file_obj.file.open('rb')
        try:
            data = file_obj.file.read(CONTENT_TEXT_MAX_BYTES + 1)
        finally:
            file_obj.file.close()
        return _decode_search_text(_cap_bytes_with_marker(data))

    kind = _document_kind(file_obj)
    if kind is None:
        return None

    # Document parsers need the full byte stream; refuse oversized sources so a
    # huge upload can't exhaust worker memory (content_text stays None).
    if (file_obj.size or 0) > DOCUMENT_SOURCE_MAX_BYTES:
        return None

    file_obj.file.open('rb')
    try:
        data = file_obj.file.read(DOCUMENT_SOURCE_MAX_BYTES + 1)
    finally:
        file_obj.file.close()
    if len(data) > DOCUMENT_SOURCE_MAX_BYTES:
        return None

    if kind == 'pdf':
        text = _extract_pdf_text(data)
    else:
        text = _extract_ooxml_text(data, kind)
    if not text.strip():
        return None
    return _cap_text_with_marker(text)


@shared_task(bind=True, max_retries=3)
def process_file_upload(self, file_id):
    """
    Process uploaded file:
    - Extract metadata (dimensions for images/videos)
    - Generate thumbnails
    - Mark as processed
    """
    from .models import File, FileType

    try:
        file_obj = File.objects.get(id=file_id)
    except File.DoesNotExist:
        logger.error(f"File {file_id} not found for processing")
        return

    try:
        # Extract metadata based on file type
        if file_obj.file_type == FileType.IMAGE:
            extract_image_metadata(file_obj)
            generate_thumbnails.delay(str(file_obj.id))

        elif file_obj.file_type == FileType.VIDEO:
            extract_video_metadata(file_obj)
            # Video thumbnails could be added with ffmpeg

        elif file_obj.file_type == FileType.AUDIO:
            extract_audio_metadata(file_obj)

        try:
            file_obj.content_text = extract_searchable_content_text(file_obj)
            file_obj.processing_error = ''
        except Exception as content_exc:  # noqa: BLE001 — extraction must not fail upload processing
            file_obj.content_text = None
            file_obj.processing_error = f'content_text extraction failed: {content_exc.__class__.__name__}'
            logger.warning(
                "Content extraction failed for file %s (%s)",
                file_obj.id,
                content_exc.__class__.__name__,
            )

        file_obj.is_processed = True
        file_obj.save(update_fields=[
            'is_processed', 'width', 'height', 'duration',
            'content_text', 'processing_error',
        ])

        logger.info(f"Processed file {file_id}: {file_obj.name}")

    except Exception as e:
        logger.error(f"Error processing file {file_id}: {e}")
        file_obj.processing_error = str(e)
        file_obj.save(update_fields=['processing_error'])
        raise self.retry(exc=e, countdown=60)


def extract_image_metadata(file_obj):
    """Extract dimensions from image file"""
    try:
        from PIL import Image

        file_obj.file.seek(0)
        with Image.open(file_obj.file) as img:
            file_obj.width, file_obj.height = img.size

    except Exception as e:
        logger.warning(f"Could not extract image metadata: {e}")


def extract_video_metadata(file_obj):
    """Extract duration and dimensions from video file"""
    # This would require ffprobe/ffmpeg
    # For now, just mark as processed
    pass


def extract_audio_metadata(file_obj):
    """Extract duration from audio file"""
    # This would require ffprobe or mutagen
    pass


@shared_task(bind=True, max_retries=3)
def generate_thumbnails(self, file_id):
    """
    Generate thumbnails for image files in multiple sizes.
    """
    from .models import File, Thumbnail, FileType
    from PIL import Image
    from django.core.files.base import ContentFile

    try:
        file_obj = File.objects.get(id=file_id)
    except File.DoesNotExist:
        logger.error(f"File {file_id} not found for thumbnail generation")
        return

    if file_obj.file_type != FileType.IMAGE:
        return

    try:
        file_obj.file.seek(0)
        with Image.open(file_obj.file) as img:
            # Convert to RGB if necessary (for PNG with transparency)
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background

            for size_name, dimensions in settings.THUMBNAIL_SIZES.items():
                # Create thumbnail
                thumb = img.copy()
                thumb.thumbnail(dimensions, Image.Resampling.LANCZOS)

                # Save to buffer
                buffer = BytesIO()
                thumb.save(buffer, format='JPEG', quality=85)
                buffer.seek(0)

                # Create or update thumbnail record
                thumbnail, created = Thumbnail.objects.update_or_create(
                    file=file_obj,
                    size=size_name,
                    defaults={
                        'width': thumb.width,
                        'height': thumb.height,
                    }
                )
                thumbnail.image.save(
                    f"{file_obj.id}_{size_name}.jpg",
                    ContentFile(buffer.read()),
                    save=True
                )

        file_obj.has_thumbnail = True
        file_obj.save(update_fields=['has_thumbnail'])

        logger.info(f"Generated thumbnails for file {file_id}")

    except Exception as e:
        logger.error(f"Error generating thumbnails for {file_id}: {e}")
        raise self.retry(exc=e, countdown=60)


@shared_task
def cleanup_trash():
    """
    Permanently delete items whose restore window has expired.
    Run this task daily via celery beat.
    """
    return purge_expired_trash()



@shared_task
def recalculate_quotas():
    """
    Recalculate storage quotas for all users.
    Includes Drive files + service storage usage.
    Useful for fixing any discrepancies.
    """
    from .models import StorageQuota, ServiceStorageUsage, File, Folder
    from django.db.models import Sum, Count

    # Get all unique owners (from Drive files, folders, and service usage)
    owner_ids = set(File.objects.values_list('owner_id', flat=True).distinct())
    owner_ids.update(Folder.objects.values_list('owner_id', flat=True).distinct())
    owner_ids.update(ServiceStorageUsage.objects.values_list('owner_id', flat=True).distinct())

    updated = 0
    for owner_id in owner_ids:
        quota, created = StorageQuota.objects.get_or_create(owner_id=owner_id)

        # Calculate Drive file usage
        file_stats = File.objects.filter(
            owner_id=owner_id,
            is_trashed=False
        ).aggregate(
            total_size=Sum('size'),
            file_count=Count('id')
        )

        folder_count = Folder.objects.filter(
            owner_id=owner_id,
            is_trashed=False
        ).count()

        quota.used_bytes = file_stats['total_size'] or 0
        quota.file_count = file_stats['file_count'] or 0
        quota.folder_count = folder_count
        quota.save()

        updated += 1

    logger.info(f"Recalculated quotas for {updated} users")
    return {'users_updated': updated}


def _version_retention_count(retention_count=None):
    if retention_count is None:
        retention_count = getattr(settings, 'DRIVE_VERSION_RETENTION_COUNT', 50)
    try:
        return max(0, int(retention_count))
    except (TypeError, ValueError):
        return 50


def prune_versions_for_file(file_obj, retention_count=None):
    """Prune old FileVersion rows for one file, oldest first.

    The live current version is represented by File.version/File.file, so rows
    whose version_number is >= File.version are guarded and never pruned even if
    they somehow exist. Among historical rows, keep the newest N and delete the
    remainder deterministically. Storage objects are deleted only when no other
    retained FileVersion row references the same file_data name.
    """
    from .models import Activity, FileVersion

    keep_count = _version_retention_count(retention_count)
    current_version = int(file_obj.version or 1)
    historical = list(
        FileVersion.objects
        .filter(file=file_obj, version_number__lt=current_version)
        .order_by('created_at', 'version_number', 'id')
    )
    if len(historical) <= keep_count:
        return {'file_id': str(file_obj.id), 'pruned': 0, 'retained': len(historical)}

    keep_ids = {
        v.id for v in sorted(
            historical,
            key=lambda v: (v.created_at, v.version_number, str(v.id)),
            reverse=True,
        )[:keep_count]
    }
    prune = [v for v in historical if v.id not in keep_ids]
    pruned = 0

    with transaction.atomic():
        for version in prune:
            storage_name = version.file_data.name
            should_delete_blob = bool(storage_name) and not FileVersion.objects.filter(
                file_data=storage_name,
            ).exclude(id=version.id).exists()
            details = {
                'event': 'version_pruned',
                'file_id': str(file_obj.id),
                'version_id': str(version.id),
                'version_number': version.version_number,
                'storage_deleted': should_delete_blob,
            }
            if should_delete_blob:
                version.file_data.delete(save=False)
            version.delete()
            Activity.objects.create(
                user_id='system',
                file=file_obj,
                action='delete',
                details=details,
            )
            pruned += 1

    return {'file_id': str(file_obj.id), 'pruned': pruned, 'retained': len(historical) - pruned}


@shared_task
def prune_file_versions(retention_count=None):
    """Idempotent scheduled job bounding per-file FileVersion history."""
    from .models import File

    keep_count = _version_retention_count(retention_count)
    files_seen = 0
    versions_pruned = 0
    for file_obj in File.objects.only('id', 'version').iterator():
        result = prune_versions_for_file(file_obj, keep_count)
        files_seen += 1
        versions_pruned += result['pruned']

    logger.info(
        'Version pruning complete: files=%s pruned=%s retention=%s',
        files_seen,
        versions_pruned,
        keep_count,
    )
    return {'files_seen': files_seen, 'versions_pruned': versions_pruned, 'retention_count': keep_count}


@shared_task
def cleanup_orphaned_files():
    """
    Clean up orphaned files in storage that don't have database records.
    """
    import os
    from django.conf import settings

    media_root = settings.MEDIA_ROOT
    files_dir = os.path.join(media_root, 'files')

    if not os.path.exists(files_dir):
        return {'orphans_deleted': 0}

    from .models import File

    orphans_deleted = 0

    # Walk through all files in storage
    for root, dirs, files in os.walk(files_dir):
        for filename in files:
            file_path = os.path.join(root, filename)
            relative_path = os.path.relpath(file_path, media_root)

            # Check if file exists in database
            exists = File.objects.filter(file=relative_path).exists()

            if not exists:
                # Check file age (only delete if older than 1 day)
                file_age = timezone.now().timestamp() - os.path.getmtime(file_path)
                if file_age > 86400:  # 1 day in seconds
                    os.remove(file_path)
                    orphans_deleted += 1
                    logger.info(f"Deleted orphaned file: {relative_path}")

    return {'orphans_deleted': orphans_deleted}


def _send_pending_tier_failure_alert(exception):
    """Push a named fail-loud alert to the configured Engineering owner."""
    import requests

    recipient = getattr(
        settings,
        'DRIVE_TIER_ALERT_RECIPIENT_EMAIL',
        'kai@shizuha.com',
    )
    id_api_url = getattr(settings, 'SHIZUHA_ID_API_URL', 'http://shizuha-id:8001/api')
    try:
        user_resp = requests.get(
            f"{id_api_url}/internal/users/by-email/",
            params={'email': recipient},
            timeout=5,
        )
        user_resp.raise_for_status()
        user_id = user_resp.json().get('id')
        if not user_id:
            raise RuntimeError(f'No Shizuha ID user for {recipient}')
        push_resp = requests.post(
            f"{id_api_url}/internal/push/send/",
            json={
                'user_id': user_id,
                'title': 'Drive tier scheduler failed',
                'body': (
                    'apply_pending_tier_changes failed; paid-tier changes may be stale. '
                    f'Owner: {recipient}. Error: {str(exception)[:240]}'
                ),
                'data': {
                    'type': 'drive_tier_scheduler_failure',
                    'mechanism': PENDING_TIER_MECHANISM,
                },
                'priority': 'high',
                'channel_id': 'default',
            },
            headers={
                'X-Internal-Service': 'shizuha-drive',
                'Content-Type': 'application/json',
            },
            timeout=5,
        )
        push_resp.raise_for_status()
    except Exception:
        logger.exception(
            'CRITICAL: %s failed and its alert to %s also failed',
            PENDING_TIER_MECHANISM,
            recipient,
        )


@shared_task
def apply_pending_tier_changes():
    """
    Apply scheduled tier changes for users whose next billing date has passed.
    Run this task daily via celery beat.
    """
    from django.db import transaction
    from django.db.models import F
    from .models import ScheduledMechanismRun, UserTier

    started_at = timezone.now()
    run, _ = ScheduledMechanismRun.objects.get_or_create(
        mechanism=PENDING_TIER_MECHANISM,
    )
    ScheduledMechanismRun.objects.filter(pk=run.pk).update(last_started_at=started_at)
    try:
        pending_ids = UserTier.objects.filter(
            pending_tier__isnull=False,
            next_billing_date__lte=started_at,
        ).values_list('id', flat=True)

        count = 0
        for user_tier_id in pending_ids:
            with transaction.atomic():
                user_tier = UserTier.objects.select_for_update().get(id=user_tier_id)
                if user_tier.apply_pending_changes(now=started_at):
                    count += 1
    except Exception as exc:
        failed_at = timezone.now()
        try:
            ScheduledMechanismRun.objects.filter(pk=run.pk).update(
                last_failed_at=failed_at,
                last_error=str(exc)[:2000],
                consecutive_failures=F('consecutive_failures') + 1,
            )
        except Exception:
            logger.exception('Could not persist pending-tier scheduler failure evidence')
        _send_pending_tier_failure_alert(exc)
        raise

    succeeded_at = timezone.now()
    ScheduledMechanismRun.objects.filter(pk=run.pk).update(
        last_succeeded_at=succeeded_at,
        last_error='',
        consecutive_failures=0,
    )
    logger.info(
        'Applied pending tier changes for %d users (last_success=%s)',
        count,
        succeeded_at.isoformat(),
    )
    return {'users_updated': count, 'last_succeeded_at': succeeded_at.isoformat()}


@shared_task
def send_share_notification(share_id):
    """
    Send notification when a file/folder is shared with a user.

    Flow:
    1) Resolve shared_with_email -> user via shizuha-id internal users endpoint.
    2) Send push via shizuha-id internal push endpoint.
    """
    from .models import Share
    import requests

    try:
        share = Share.objects.get(id=share_id)
    except Share.DoesNotExist:
        return

    if not share.shared_with_email:
        return

    # Get target name
    target_name = share.file.name if share.file else share.folder.name

    id_api_url = getattr(settings, 'SHIZUHA_ID_API_URL', 'http://shizuha-id:8001/api')

    try:
        # Step 1: Resolve user by email
        user_resp = requests.get(
            f"{id_api_url}/internal/users/by-email/",
            params={'email': share.shared_with_email},
            timeout=5,
        )
        if user_resp.status_code != 200:
            logger.info(
                "Skipping share push notification: user not found for email %s (status=%s)",
                share.shared_with_email,
                user_resp.status_code,
            )
            return

        user_data = user_resp.json()
        user_id = user_data.get('id')
        if not user_id:
            logger.warning("Share notification user lookup returned no user id for %s", share.shared_with_email)
            return

        # Step 2: Send push
        push_resp = requests.post(
            f"{id_api_url}/internal/push/send/",
            json={
                'user_id': user_id,
                'title': 'File shared with you',
                'body': f'"{target_name}" has been shared with you',
                'data': {
                    'type': 'drive_share',
                    'screen': 'DriveTab',
                    'share_id': str(share.id),
                    'share_type': share.share_type,
                },
                'priority': 'high',
                'channel_id': 'default',
            },
            headers={
                'X-Internal-Service': 'shizuha-drive',
                'Content-Type': 'application/json',
            },
            timeout=5,
        )
        if not push_resp.ok:
            logger.warning(
                "Share push failed for user %s (status=%s): %s",
                user_id,
                push_resp.status_code,
                push_resp.text,
            )
    except Exception as e:
        logger.warning(f"Failed to send share notification: {e}")


@shared_task
def purge_expired_trash():
    """DRV-7: permanently delete trashed File/Folder items older than the
    configured retention window (settings.DRIVE_TRASH_RETENTION_DAYS, default
    30). Restore works within the window; this only removes items past it.
    Files go through delete_permanently() so blobs/thumbnails/versions/shares
    and the storage-quota post_delete signal are all handled."""
    from .models import File, Folder

    days = getattr(settings, 'DRIVE_TRASH_RESTORE_WINDOW_DAYS', None)
    if days is None:
        days = getattr(settings, 'DRIVE_TRASH_RETENTION_DAYS', 30)
    cutoff = timezone.now() - timedelta(days=days)

    purged_files = 0
    expired_filter = Q(restore_until__isnull=False, restore_until__lt=timezone.now()) | Q(
        restore_until__isnull=True,
        trashed_at__isnull=False,
        trashed_at__lt=cutoff,
    )
    for f in File.objects.filter(is_trashed=True).filter(expired_filter):
        try:
            f.delete_permanently()
            purged_files += 1
        except Exception:
            logger.exception("purge_expired_trash: failed to purge file %s", f.pk)

    expired_folders = Folder.objects.filter(
        is_trashed=True, trashed_at__isnull=False, trashed_at__lt=cutoff)
    purged_folders = expired_folders.count()
    expired_folders.delete()

    logger.info(
        "purge_expired_trash: purged %d files + %d folders trashed before %s (retention=%dd)",
        purged_files, purged_folders, cutoff.isoformat(), days)
    return {'purged_files': purged_files, 'purged_folders': purged_folders, 'retention_days': days}


@shared_task
def purge_dead_shares():
    """Hard-delete Share rows that are revoked/expired AND past the grace window.

    INV-5 (DRV-28 / HLD §5): purge must NEVER destroy the audit trail.
    ShareAccessLog.share uses on_delete=SET_NULL so rows survive; share_id_copy
    keeps them queryable. Log retention >= 1 year — only Shares (not logs) are
    purged. Run daily via celery beat.
    """
    from .models import Share

    GRACE_DAYS = getattr(settings, 'DEAD_SHARE_GRACE_DAYS', 30)
    LOG_RETENTION_DAYS = getattr(settings, 'SHARE_LOG_RETENTION_DAYS', 365)
    cutoff = timezone.now() - timedelta(days=GRACE_DAYS)
    log_cutoff = timezone.now() - timedelta(days=LOG_RETENTION_DAYS)

    dead = Share.objects.filter(
        updated_at__lt=cutoff,
    ).filter(
        Q(is_active=False) | Q(expires_at__lt=cutoff)
    )
    count = dead.count()
    dead.delete()  # on_delete=SET_NULL nullifies access_log.share (INV-5)

    from .models import ShareAccessLog
    old_logs = ShareAccessLog.objects.filter(accessed_at__lt=log_cutoff)
    log_count = old_logs.count()
    old_logs.delete()

    logger.info('purge_dead_shares: deleted %d shares, %d old log rows', count, log_count)
    return {'shares_purged': count, 'log_rows_trimmed': log_count}


@shared_task(name='drive.record_file_activity')
def record_file_activity(*, user_id, action, file_id=None, folder_id=None, created_at=None):
    """Background write for DRV-4 Slice 6 Recent activity.

    Views enqueue this task after commit so file-open/preview/download/edit
    response latency pays only the broker enqueue cost; the task itself performs
    the 10-minute DB collapse as a second safety net if cache throttling missed.
    """
    from django.utils.dateparse import parse_datetime

    from .models import File, FileActivity, Folder

    at = parse_datetime(created_at) if created_at else None
    file_obj = File.objects.get(id=file_id) if file_id else None
    folder_obj = Folder.objects.get(id=folder_id) if folder_id else None
    activity, created = FileActivity.record(
        user_id=user_id,
        action=action,
        file=file_obj,
        folder=folder_obj,
        at=at,
    )
    return {'id': str(activity.id), 'created': created}


def _send_offboarding_alert(content):
    """Best-effort Connect DM to Drive's named owner; return durable status."""
    import requests

    owner = getattr(settings, 'DRIVE_OFFBOARDING_ALERT_OWNER_USERNAME', 'ryo').strip()
    token = getattr(settings, 'DRIVE_OFFBOARDING_CONNECT_TOKEN', '').strip()
    base = getattr(settings, 'DRIVE_OFFBOARDING_CONNECT_API_URL', '').rstrip('/')
    if not owner:
        return 'failed:no-owner'
    if not token:
        return 'failed:no-token'
    try:
        response = requests.post(
            f'{base}/messaging/dm/',
            json={'recipient_username': owner, 'content': content[:4000]},
            headers={'Authorization': f'Bearer {token}'},
            timeout=5,
        )
        if response.status_code >= 400:
            return f'failed:http-{response.status_code}'
        return f'sent:{owner}'
    except Exception as exc:
        logger.exception('offboarding fail-loud Connect DM failed')
        return f'failed:{type(exc).__name__}'


def _offboarding_state():
    from .models import DeletionProcessorState

    state, _ = DeletionProcessorState.objects.get_or_create(
        pk=DeletionProcessorState.SINGLETON_PK,
    )
    return state


@shared_task
def process_expired_deletion_requests():
    """VEN-153: permanently delete data for users whose grace period has expired.

    Finds all pending DeletionRequests past their grace_expires_at, permanently
    deletes all files/folders/shares for that user, purges or anonymises all
    residual user-scoped records, and marks the request as completed. Runs daily
    via celery beat.

    GDPR compliance: every user-linked table is either purged or anonymised so
    that no identifiable data remains after completion. The DeletionRequest row
    is retained as an audit trail but its email/reason fields are cleared.
    """
    from .models import (
        DeletionRequest, File, Folder, Share, StorageQuota,
        ResumableUpload, Activity, FileActivity, ShareAccessLog,
        ServiceStorageUsage,
    )

    now = timezone.now()
    state = _offboarding_state()
    state.last_started_at = now
    state.save(update_fields=['last_started_at', 'updated_at'])
    expired = DeletionRequest.objects.filter(
        status=DeletionRequest.STATUS_PENDING,
        grace_expires_at__lte=now,
    )
    processed = 0
    failed = 0
    failed_request_ids = []
    for candidate in expired.iterator():
        dr = candidate
        try:
            with transaction.atomic():
                # Serialize workers on one request and re-check eligibility after
                # taking the lock. A delayed duplicate worker must not repeat an
                # irreversible storage purge after another worker completed it.
                dr = DeletionRequest.objects.select_for_update().get(pk=candidate.pk)
                if (
                    dr.status != DeletionRequest.STATUS_PENDING
                    or dr.grace_expires_at > now
                ):
                    continue
                owner_id = dr.owner_id
                failures = 0

                # Capture every audit row that targets an owner resource before
                # FK SET_NULL cascades erase the relational lookup path. This
                # includes collaborator-authored events whose actor is not the
                # offboarding user.
                file_ids = list(File.objects.filter(owner_id=owner_id).values_list('id', flat=True))
                folder_ids = list(Folder.objects.filter(owner_id=owner_id).values_list('id', flat=True))
                resource_ids = file_ids + folder_ids
                target_activity_ids = list(Activity.objects.filter(
                    Q(file_id__in=file_ids)
                    | Q(folder_id__in=folder_ids)
                    | Q(resource_id__in=resource_ids)
                    | Q(parent_id__in=folder_ids)
                ).values_list('id', flat=True))

                # Permanently delete all files (blobs + records)
                for f in File.objects.filter(id__in=file_ids):
                    try:
                        f.delete_permanently()
                    except Exception:
                        logger.exception(
                            "process_expired_deletion: failed to purge file %s for %s",
                            f.id, owner_id,
                        )
                        failures += 1

                if failures > 0:
                    logger.error(
                        "process_expired_deletion: %d file(s) failed to purge for %s, "
                        "not marking request %s as completed",
                        failures, owner_id, dr.id,
                    )
                    failed += 1
                    failed_request_ids.append(str(dr.id))
                    continue

                # Delete folders (soft-deleted or not)
                Folder.objects.filter(owner_id=owner_id).delete()

                # Delete shares created by this user
                Share.objects.filter(created_by_id=owner_id).delete()

                # Delete shares RECEIVED by this user (shared_with_id)
                Share.objects.filter(shared_with_id=owner_id).delete()

                # Reset storage quota
                StorageQuota.objects.filter(owner_id=owner_id).delete()

                # --- Residual user-scoped data purge (GDPR compliance) ---

                # 1. ResumableUpload: purge shared or legacy local staging bytes
                # before the row. Active sessions normally have file=None.
                from drive.resumable_storage import delete_staging, is_shared_staging_path
                resumable_root = (Path(settings.MEDIA_ROOT) / 'resumable_uploads').resolve()
                for ru in ResumableUpload.objects.filter(owner_id=owner_id):
                    try:
                        if is_shared_staging_path(ru.temp_path):
                            delete_staging(ru.temp_path)
                        elif ru.temp_path and os.path.lexists(ru.temp_path):
                            temp_path = Path(ru.temp_path)
                            resolved_temp_path = temp_path.resolve()
                            try:
                                resolved_temp_path.relative_to(resumable_root)
                            except ValueError as exc:
                                raise ValueError(
                                    f'resumable temp path is outside managed root: {resolved_temp_path}'
                                ) from exc
                            os.remove(temp_path)
                        # Delete the DB retry target only after the temp bytes are
                        # gone (or confirmed absent). A failed unlink leaves this
                        # row for the next task run.
                        ru.delete()
                    except Exception:
                        logger.exception(
                            "process_expired_deletion: failed to delete temp_path %s for %s",
                            ru.temp_path, owner_id,
                        )
                        failures += 1

                if failures > 0:
                    logger.error(
                        "process_expired_deletion: %d residual purge(s) failed for %s, "
                        "not marking request %s as completed",
                        failures, owner_id, dr.id,
                    )
                    failed += 1
                    failed_request_ids.append(str(dr.id))
                    continue

                # 2. Activity: anonymise (null out user_id/actor_id, resource_name,
                #    metadata/details) so the audit trail shape is preserved but no
                #    PII remains.
                Activity.objects.filter(id__in=target_activity_ids).update(
                    file=None, folder=None, resource_id=None, resource_type='',
                    resource_name='', parent_id=None, details={}, metadata={},
                )
                Activity.objects.filter(user_id=owner_id).update(
                    user_id='', actor_id='', file=None, folder=None,
                    resource_id=None, resource_type='', resource_name='',
                    parent_id=None, details={}, metadata={},
                )
                Activity.objects.filter(actor_id=owner_id).update(
                    actor_id='',
                )

                # 3. FileActivity: purge rows owned by this user
                FileActivity.objects.filter(user_id=owner_id).delete()

                # 4. ShareAccessLog: anonymise viewer_id, IP, user_agent
                ShareAccessLog.objects.filter(viewer_id=owner_id).update(
                    viewer_id=None, viewer_ip=None, user_agent='',
                )

                # 5. ServiceStorageUsage: purge rows for this user
                ServiceStorageUsage.objects.filter(owner_id=owner_id).delete()

                # 6. Anonymise the DeletionRequest itself. Status/timestamps retain
                #    the non-identifying audit shape; stable owner identity, email,
                #    and free-text reason are erased before completion.
                dr.owner_id = ''
                dr.email = ''
                dr.reason = ''
                dr.save(update_fields=['owner_id', 'email', 'reason', 'updated_at'])

                dr.complete()
                processed += 1
        except Exception:
            failed += 1
            failed_request_ids.append(str(dr.id))
            logger.exception(
                "process_expired_deletion: failed to process request %s for %s",
                dr.id, dr.owner_id,
            )

    finished = timezone.now()
    pending = DeletionRequest.objects.filter(
        status=DeletionRequest.STATUS_PENDING,
        grace_expires_at__lte=finished,
    ).count()
    state = _offboarding_state()
    state.last_processed_count = processed
    state.last_failed_count = failed
    state.expired_pending_count = pending
    if failed:
        state.last_failure_at = finished
        state.consecutive_failures += 1
        request_sample = ', '.join(failed_request_ids[:5])
        state.last_error = (
            f'{failed} deletion request(s) failed; pending expired={pending}; '
            f'request IDs={request_sample}'
        )[:2000]
        alert = _send_offboarding_alert(
            '🔴 Drive offboarding deletion run failed closed.\n'
            f'Failed requests: {failed}; expired pending: {pending}; '
            f'request IDs: {request_sample or "unknown"}.\n'
            'Owner: Drive Engineering. Inspect the VEN-153 processor state and '
            'storage retry rows; never mark requests completed manually.'
        )
        state.last_alert_at = finished
        state.last_alert_status = alert
    else:
        state.last_success_at = finished
        state.last_error = ''
        state.last_alert_status = ''
        state.consecutive_failures = 0
    state.save()

    logger.info(
        "process_expired_deletion_requests: processed=%d failed=%d pending=%d alert=%s",
        processed, failed, pending, state.last_alert_status,
    )
    return {
        'processed': processed,
        'failed': failed,
        'expired_pending': pending,
        'alert_status': state.last_alert_status if failed else '',
    }


@shared_task
def probe_expired_deletion_requests():
    """Fail-loud invariant probe for silently stalled compliance deletion."""
    from .models import DeletionRequest

    now = timezone.now()
    stale_after = timedelta(hours=getattr(settings, 'DRIVE_OFFBOARDING_STALE_HOURS', 36))
    expired = DeletionRequest.objects.filter(
        status=DeletionRequest.STATUS_PENDING,
        grace_expires_at__lte=now,
    )
    pending = expired.count()
    oldest = expired.order_by('grace_expires_at').values_list('grace_expires_at', flat=True).first()
    state = _offboarding_state()
    state.last_probe_at = now
    state.expired_pending_count = pending
    stale_success = pending > 0 and (
        state.last_success_at is None or state.last_success_at < now - stale_after
    )
    overdue = bool(oldest and oldest < now - stale_after)
    healthy = not (stale_success or overdue or state.consecutive_failures > 0)
    if not healthy:
        alert = _send_offboarding_alert(
            '🔴 Drive offboarding invariant probe is unhealthy.\n'
            f'Expired pending requests: {pending}; consecutive failed runs: '
            f'{state.consecutive_failures}; last success: '
            f'{state.last_success_at.isoformat() if state.last_success_at else "never"}.\n'
            'Invariant: expired requests must be processed fail-closed and never silently stall.'
        )
        state.last_alert_at = now
        state.last_alert_status = alert
    state.save()
    return {
        'healthy': healthy,
        'expired_pending': pending,
        'last_success_at': state.last_success_at.isoformat() if state.last_success_at else None,
        'alert_status': state.last_alert_status if not healthy else '',
    }
