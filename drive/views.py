"""
API Views for Shizuha Drive
"""
import logging
import secrets
import os
import uuid
import hashlib
import hmac
import base64
import json
import time
import html
import magic
import tempfile
from datetime import timedelta, timezone as dt_timezone
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.cache import cache
from django.db import connection, IntegrityError
from django.core.files import File as DjangoFile
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.db.models import Case, FloatField, Max, Q, Sum, Value, When
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db.models.functions import Coalesce, Lower
from django.utils.http import content_disposition_header
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import BaseParser, MultiPartParser, FormParser, JSONParser
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Folder, File, FileVersion, Share, StorageQuota, Activity, FileActivity,
    ServiceStorageUsage, StorageQuotaReservation, StorageTier, UserTier,
    ShareType, SharePermission, ShareAccessLog, ResumableUpload,
    WaitlistEntry,  # DRV-22
    DeletionRequest, DeletionProcessorState,  # VEN-153
)
from .serializers import (
    FolderSerializer, FolderListSerializer, FolderTreeSerializer,
    FileSerializer, FileListSerializer, FileUploadSerializer,
    FileVersionSerializer,
    ShareSerializer, ShareCreateSerializer, ShareAccessLogSerializer,
    StorageQuotaSerializer, ActivitySerializer,
    StorageReportSerializer, CanUploadSerializer,
    WaitlistEntrySerializer,  # DRV-22
    ResumableUploadCreateSerializer, ResumableUploadSerializer,
    StorageTierSerializer, UserTierSerializer, UserTierScheduleSerializer,
)
from .permissions import (
    IsOwner, IsOwnerOrShared, RequireSharePermission, accessible_object_ids,
    require_share_access,
)
from .tasks import process_file_upload, generate_thumbnails, record_file_activity
from .visibility import (
    apply_authorized_filters, apply_name_search, visible_activity, visible_file_activity,
    user_share_grant_q, visible_files, visible_folders,
)
from .resumable_storage import (
    create_staging_path,
    delete_staging,
    iter_staged_blocks,
    read_staged_range,
    write_chunk,
)

logger = logging.getLogger(__name__)


def _validation_has_code(detail, expected):
    """Return whether a nested DRF validation detail carries ``expected``."""
    if isinstance(detail, dict):
        return any(_validation_has_code(value, expected) for value in detail.values())
    if isinstance(detail, (list, tuple)):
        return any(_validation_has_code(value, expected) for value in detail)
    return getattr(detail, 'code', None) == expected

def _actor_id(request) -> str:
    actor = getattr(getattr(request, 'user', None), 'id', None)
    if actor in (None, ''):
        raise PermissionDenied('Authenticated actor is required for audit logging.')
    return str(actor)


def _record_activity_event(request, event_type: str, resource, metadata: dict | None = None) -> Activity:
    return Activity.record_event(
        event_type=event_type,
        actor_id=_actor_id(request),
        resource=resource,
        metadata=metadata or {},
    )


def _file_activity_cache_key(user_id: str, action: str, *, file_id=None, folder_id=None) -> str:
    target = f"file:{file_id}" if file_id else f"folder:{folder_id}"
    return f"drive:file-activity:{user_id}:{action}:{target}"


def _enqueue_file_activity(request, action: str, *, file_obj=None, folder_obj=None) -> float:
    """Queue one FileActivity write without making access endpoints wait on DB I/O.

    Production uses Celery after transaction commit; sqlite/test execution records
    inline so regression tests can assert rows without a broker. A best-effort
    cache key collapses repeated caller+target+action events for 10 minutes before
    they even reach the worker; FileActivity.record repeats the collapse in DB.
    Returns enqueue scheduling latency in milliseconds for the DRV-24 ≤5 ms SLO.
    """
    started = time.monotonic()
    user_id = _actor_id(request)
    if bool(file_obj) == bool(folder_obj):
        raise ValueError('exactly one file_obj or folder_obj is required')
    file_id = str(file_obj.id) if file_obj is not None else None
    folder_id = str(folder_obj.id) if folder_obj is not None else None
    collapse_seconds = getattr(settings, 'DRIVE_FILE_ACTIVITY_COLLAPSE_SECONDS', 10 * 60)
    try:
        admitted = cache.add(
            _file_activity_cache_key(user_id, action, file_id=file_id, folder_id=folder_id),
            True,
            timeout=collapse_seconds,
        )
    except Exception:  # noqa: BLE001 — cache outage must not break file access
        admitted = True
    if admitted is False:
        return (time.monotonic() - started) * 1000

    payload = {
        'user_id': user_id,
        'action': action,
        'file_id': file_id,
        'folder_id': folder_id,
        'created_at': timezone.now().isoformat(),
    }

    def enqueue():
        try:
            if connection.vendor != 'postgresql' or getattr(settings, 'DRIVE_FILE_ACTIVITY_INLINE', False):
                target_file = file_obj if file_obj is not None else None
                target_folder = folder_obj if folder_obj is not None else None
                FileActivity.record(user_id=user_id, action=action, file=target_file, folder=target_folder)
            else:
                record_file_activity.delay(**payload)
        except Exception:  # noqa: BLE001 — access must stay available if recent logging is degraded
            logger.warning('FileActivity enqueue failed action=%s file=%s folder=%s', action, file_id, folder_id, exc_info=True)

    if connection.vendor != 'postgresql' or getattr(settings, 'DRIVE_FILE_ACTIVITY_INLINE', False):
        enqueue()
    else:
        transaction.on_commit(enqueue)
    elapsed_ms = (time.monotonic() - started) * 1000
    if elapsed_ms > 5:
        logger.warning('FileActivity enqueue scheduling exceeded SLO: %.3f ms action=%s', elapsed_ms, action)
    return elapsed_ms

def _request_id(request) -> str:
    return request.headers.get('X-Request-ID') or str(uuid.uuid4())


def _upload_error(code: str, message: str, request_id: str, **details):
    payload = {'error': code, 'message': message, 'request_id': request_id}
    if details:
        payload['details'] = details
    return payload


def _validation_message(detail) -> str:
    if isinstance(detail, dict):
        return '; '.join(f"{k}: {v}" for k, v in detail.items()) or 'Invalid upload request.'
    if isinstance(detail, list):
        return '; '.join(str(v) for v in detail) or 'Invalid upload request.'
    return str(detail) or 'Invalid upload request.'


def _truthy(value) -> bool:
    return str(value).lower() in {'1', 'true', 'yes', 'on'}


def _parse_bool_param(value, *, default=False):
    if value in (None, ''):
        return default
    return _truthy(value)


def _parse_iso_datetime_param(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise ValidationError(f'Invalid datetime: {value}')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _list_query_values(params, *names):
    values = []
    for name in names:
        for raw in params.getlist(name):
            values.extend(part.strip() for part in str(raw).split(',') if part.strip())
    return values


def _decode_search_cursor(raw):
    if not raw:
        return None
    try:
        padded = str(raw) + ('=' * (-len(str(raw)) % 4))
        return json.loads(base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8'))
    except Exception as exc:  # noqa: BLE001
        raise ValidationError('Invalid cursor') from exc


def _encode_search_cursor(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _highlight_snippet(text, term, *, window=80):
    text = text or ''
    term = (term or '').strip()
    if not text:
        return ''
    if not term:
        return html.escape(text[:window])
    lowered = text.lower()
    needle = term.lower()
    idx = lowered.find(needle)
    if idx < 0:
        return html.escape(text[:window])
    start = max(idx - window // 2, 0)
    end = min(idx + len(term) + window // 2, len(text))
    prefix = '…' if start > 0 else ''
    suffix = '…' if end < len(text) else ''
    before = html.escape(text[start:idx])
    match = html.escape(text[idx:idx + len(term)])
    after = html.escape(text[idx + len(term):end])
    return f'{prefix}{before}<mark>{match}</mark>{after}{suffix}'


def _explicit_org_id(request):
    """Return caller-supplied org scope from header/query, or None if omitted."""
    headers = getattr(request, 'headers', {}) or {}
    query_params = getattr(request, 'query_params', {}) or {}
    raw = (
        headers.get('X-Organization-ID')
        or query_params.get('organization')
        or query_params.get('organization_id')
    )
    if raw in (None, ''):
        return None
    return str(raw)


def _user_org_id_set(user) -> set[str]:
    values = set()
    memberships = getattr(user, 'organization_memberships', None) or {}
    if isinstance(memberships, dict):
        values.update(str(k) for k in memberships.keys() if k not in (None, ''))
    for attr in ('organization_ids', 'org_ids'):
        raw = getattr(user, attr, None)
        if isinstance(raw, str):
            values.add(raw)
        elif raw:
            values.update(str(v) for v in raw if v not in (None, ''))
    org = getattr(user, 'organization_id', None)
    if org not in (None, ''):
        values.add(str(org))
    return values


def _ensure_explicit_org_membership(request):
    """Wrong-org explicit scopes must fail closed (403), not become 200/201."""
    org_id = _explicit_org_id(request)
    if org_id is None:
        return None
    user = request.user
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return org_id
    if org_id not in _user_org_id_set(user):
        raise PermissionDenied(f'You are not a member of organization {org_id}.')
    return org_id



class ExplicitOrgMembershipMixin:
    """Deny explicit wrong-org scopes before any authenticated Drive action."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if getattr(request.user, 'is_authenticated', False):
            _ensure_explicit_org_membership(request)

def _file_sha256(field_file) -> str:
    """Compute SHA-256 over the stored bytes without streaming them to a client."""
    digest = hashlib.sha256()
    field_file.open('rb')
    try:
        for chunk in field_file.chunks():
            digest.update(chunk)
    finally:
        field_file.close()
    return digest.hexdigest()

class OctetStreamParser(BaseParser):
    """Accept raw chunk bytes while views read from the underlying request body."""
    media_type = 'application/octet-stream'

    def parse(self, stream, media_type=None, parser_context=None):
        return {}


def _parse_http_range(range_header: str, total_size: int):
    """Parse a single HTTP bytes range, returning (start, end) or None."""
    if not range_header or not range_header.startswith('bytes=') or ',' in range_header:
        return None
    spec = range_header[len('bytes='):]
    if '-' not in spec:
        return None
    start_text, end_text = spec.split('-', 1)
    try:
        if start_text == '':
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(total_size - suffix_length, 0)
            end = total_size - 1
        else:
            start = int(start_text)
            end = total_size - 1 if end_text == '' else int(end_text)
    except ValueError:
        return None
    if start < 0 or end < start or start >= total_size:
        return None
    return start, min(end, total_size - 1)


def _stored_sha256(file_obj: File) -> str:
    return getattr(file_obj, 'checksum', '') or getattr(file_obj, 'checksum_sha256', '') or ''


def _download_integrity_headers(file_obj: File, *, partial: bool) -> dict:
    stored_checksum = _stored_sha256(file_obj)
    headers = {
        'Accept-Ranges': 'bytes',
        'X-Checksum-Available': 'true' if stored_checksum else 'false',
        'X-Drive-Integrity': 'partial-range' if partial else (
            'verified' if stored_checksum else 'unverified-legacy'
        ),
    }
    if stored_checksum:
        headers['X-Drive-Checksum-SHA256'] = stored_checksum
    return headers


def _enqueue_file_processing(file_obj: File) -> None:
    """Best-effort async processing; upload success must not depend on broker health."""
    try:
        process_file_upload.delay(str(file_obj.id))
    except Exception as exc:  # noqa: BLE001 — non-critical post-upload work
        logger.warning(
            "Skipping async processing enqueue for file %s after upload commit: %s",
            file_obj.id, exc,
            exc_info=True,
        )


class FolderViewSet(ExplicitOrgMembershipMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing folders.

    list: Get all folders for the authenticated user
    create: Create a new folder
    retrieve: Get a specific folder
    update: Update a folder
    destroy: Move folder to trash (soft delete)
    """
    permission_classes = [permissions.IsAuthenticated, IsOwner]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['parent', 'is_starred', 'is_trashed']

    # DRV-13 H-1: folder read/mutate actions honor granular shares.
    # Destroy (trash), restore, and star stay OWNER-ONLY.
    share_required_level = {
        'retrieve': SharePermission.VIEW,
        'capabilities': SharePermission.VIEW,
        'update': SharePermission.EDIT,
        'partial_update': SharePermission.EDIT,
        'move': SharePermission.EDIT,
    }

    def get_permissions(self):
        # Mutate actions in the map enforce the granular share level; everything
        # else (incl. destroy / restore / star / share-management) stays owner-only.
        if getattr(self, 'action', None) in self.share_required_level:
            return [permissions.IsAuthenticated(), RequireSharePermission()]
        return [permissions.IsAuthenticated(), IsOwner()]

    def get_serializer_class(self):
        if self.action == 'list':
            return FolderListSerializer
        if self.action == 'tree':
            return FolderTreeSerializer
        return FolderSerializer

    def get_queryset(self):
        user = self.request.user
        # DRV-13: for share-honoring mutate actions, broaden the queryset so a
        # grantee's folder is FINDABLE by get_object; RequireSharePermission then
        # enforces the actual level (INV-1). List/read stay owner-scoped.
        if getattr(self, 'action', None) in self.share_required_level:
            shared_ids = accessible_object_ids(user, Folder)
            return Folder.objects.filter(Q(owner_id=user.id) | Q(id__in=shared_ids))

        if self.action in ('list', 'root', 'tree'):
            include_trashed = _truthy(self.request.query_params.get('trash')) or _truthy(self.request.query_params.get('is_trashed'))
            queryset = visible_folders(user, include_trashed=include_trashed)
            return apply_authorized_filters(queryset, self.request)

        return Folder.objects.filter(owner_id=user.id)

    def perform_create(self, serializer):
        # DRV-38 AC1: mutation + audit row commit or roll back together.
        with transaction.atomic():
            serializer.save(owner_id=self.request.user.id)
            _record_activity_event(
                self.request,
                Activity.EVENT_CREATE,
                serializer.instance,
                {'name': serializer.instance.name},
            )
            # DRV-105: record the folder create mutation in the change feed.
            from .change_feed import Change, record_change, snapshot_folder
            record_change(
                owner_id=serializer.instance.owner_id,
                resource_type=Change.RESOURCE_FOLDER,
                resource_id=serializer.instance.id,
                mutation='create',
                generation=1,
                parent_id=serializer.instance.parent_id,
                name=serializer.instance.name,
                payload=snapshot_folder(serializer.instance),
            )

    def perform_destroy(self, instance):
        # Soft delete - move to trash (atomic with its audit row, DRV-38 AC1)
        with transaction.atomic():
            instance.move_to_trash()
            _record_activity_event(
                self.request,
                Activity.EVENT_DELETE,
                instance,
                {'name': instance.name, 'soft': True, 'hard': False},
            )
            # DRV-105: record the folder trash mutation in the change feed.
            from .change_feed import Change, record_change, snapshot_folder
            record_change(
                owner_id=instance.owner_id,
                resource_type=Change.RESOURCE_FOLDER,
                resource_id=instance.id,
                mutation='trash',
                generation=1,
                parent_id=instance.parent_id,
                name=instance.name,
                payload=snapshot_folder(instance),
            )

    @action(detail=False, methods=['get'])
    def root(self, request):
        """Get root folders (no parent)"""
        folders = self.get_queryset().filter(parent=None, is_trashed=False)
        serializer = FolderListSerializer(folders, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def capabilities(self, request, pk=None):
        """Return caller capabilities without mutating the folder.

        The object must already be visible to the caller.  This keeps hidden
        folder IDs undisclosed while allowing trusted service callers to prove
        EDIT access before building or downloading a large publication pack.
        """
        folder = self.get_object()
        return Response({
            'folder_id': str(folder.id),
            'can_view': True,
            'can_edit': require_share_access(
                request.user,
                folder,
                SharePermission.EDIT,
            ),
        })

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """Get full folder tree"""
        root_folders = self.get_queryset().filter(parent=None, is_trashed=False)
        serializer = FolderTreeSerializer(root_folders, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def children(self, request):
        """Lazy-load children of a folder (or root folders if no parent_id).

        DRV-29 Slice 8: replaces the full recursive tree for the sidebar.
        Returns one level of children with file_count and total_size.
        """
        parent_id = request.query_params.get('parent')
        qs = self.get_queryset().filter(is_trashed=False)
        if parent_id:
            qs = qs.filter(parent_id=parent_id)
        else:
            qs = qs.filter(parent=None)
        qs = qs.order_by('name')
        serializer = FolderListSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore folder from trash"""
        folder = self.get_object()
        # DRV-38 AC1: the multi-row restore + its audit row are one transaction.
        with transaction.atomic():
            folder.is_trashed = False
            folder.trashed_at = None
            folder.save()

            # Also restore files and subfolders
            folder.files.update(is_trashed=False, trashed_at=None)
            for child in folder.get_descendants():
                child.is_trashed = False
                child.trashed_at = None
                child.save()

            _record_activity_event(request, Activity.EVENT_RESTORE, folder, {'name': folder.name})
            # DRV-105: record the folder restore mutation in the change feed.
            from .change_feed import Change, record_change, snapshot_folder
            record_change(
                owner_id=folder.owner_id,
                resource_type=Change.RESOURCE_FOLDER,
                resource_id=folder.id,
                mutation='restore',
                generation=1,
                parent_id=folder.parent_id,
                name=folder.name,
                payload=snapshot_folder(folder),
            )

        return Response(FolderSerializer(folder).data)

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        """Move folder to a new parent"""
        folder = self.get_object()
        from_parent_id = folder.parent_id
        new_parent_id = request.data.get('parent')

        if new_parent_id:
            try:
                new_parent = Folder.objects.get(id=new_parent_id, owner_id=request.user.id)
                # Prevent moving to self or descendant
                if new_parent == folder or new_parent in folder.get_descendants():
                    return Response(
                        {'error': 'Cannot move folder to itself or its descendants'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                folder.parent = new_parent
            except Folder.DoesNotExist:
                return Response(
                    {'error': 'Target folder not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            folder.parent = None

        # DRV-38 AC1: move + audit row commit or roll back together.
        with transaction.atomic():
            folder.save()
            _record_activity_event(
                request,
                Activity.EVENT_MOVE,
                folder,
                {
                    'name': folder.name,
                    'from_parent_id': str(from_parent_id) if from_parent_id else None,
                    'to_parent_id': str(new_parent_id) if new_parent_id else None,
                },
            )
            # DRV-105: record the folder move mutation in the change feed.
            from .change_feed import Change, record_change, snapshot_folder
            record_change(
                owner_id=folder.owner_id,
                resource_type=Change.RESOURCE_FOLDER,
                resource_id=folder.id,
                mutation='move',
                generation=1,
                parent_id=folder.parent_id,
                name=folder.name,
                payload=snapshot_folder(folder),
            )

        return Response(FolderSerializer(folder).data)


def _drive_detect_file_type(mime_type):
    if mime_type.startswith('image/'):
        return 'image'
    if mime_type.startswith('video/'):
        return 'video'
    if mime_type.startswith('audio/'):
        return 'audio'
    if mime_type in ['application/pdf', 'text/plain'] or mime_type.startswith('application/vnd.openxmlformats-officedocument.wordprocessingml'):
        return 'document'
    if mime_type.startswith('application/vnd.ms-excel') or mime_type.startswith('application/vnd.openxmlformats-officedocument.spreadsheetml'):
        return 'spreadsheet'
    if mime_type.startswith('application/vnd.ms-powerpoint') or mime_type.startswith('application/vnd.openxmlformats-officedocument.presentationml'):
        return 'presentation'
    if mime_type in ['application/zip', 'application/x-rar-compressed', 'application/x-tar']:
        return 'archive'
    if mime_type in ['text/x-python', 'application/javascript', 'text/html', 'text/css']:
        return 'code'
    return 'other'


class ResumableUploadViewSet(ExplicitOrgMembershipMixin, viewsets.ViewSet):
    """Tus-style resumable uploads: create session, PUT chunks by offset, complete."""
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [OctetStreamParser, MultiPartParser, FormParser, JSONParser]

    def _error(self, code, message, http_status, *, retryable=False, details=None, extra=None):
        payload = {
            'error': {
                'code': code,
                'message': message,
                'retryable': retryable,
                'details': details or {},
            },
        }
        if extra:
            payload.update(extra)
        return Response(payload, status=http_status)

    def _get_session(self, request, pk):
        try:
            session = ResumableUpload.objects.get(id=pk, owner_id=request.user.id)
        except (ResumableUpload.DoesNotExist, ValueError):
            raise Http404("Upload session not found")
        if session.is_expired and session.status == ResumableUpload.Status.ACTIVE:
            session.status = ResumableUpload.Status.ABORTED
            session.save(update_fields=['status', 'updated_at'])
            raise Http404("Upload session expired")
        return session

    def _read_upload_body(self, request):
        upload = request.FILES.get('chunk') or request.FILES.get('file')
        if upload is not None:
            return b''.join(upload.chunks()), upload.size
        body = request._request.body
        return body, len(body)

    def _parse_content_range(self, request, body_size):
        header = request.headers.get('Content-Range')
        if not header:
            try:
                offset = int(request.data.get('offset', request.headers.get('Upload-Offset', -1)))
            except (TypeError, ValueError):
                offset = -1
            if offset < 0:
                return None
            return offset, offset + body_size - 1, None
        prefix = 'bytes '
        if not header.startswith(prefix) or '-' not in header or '/' not in header:
            return None
        try:
            range_part, total_part = header[len(prefix):].split('/', 1)
            start_text, end_text = range_part.split('-', 1)
            start = int(start_text)
            end = int(end_text)
            total = None if total_part == '*' else int(total_part)
        except ValueError:
            return None
        return start, end, total

    def create(self, request):
        serializer = ResumableUploadCreateSerializer(data=request.data, context={'request': request})
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            if _validation_has_code(
                exc.detail,
                'folder_not_found_or_not_editable',
            ):
                return self._error(
                    'folder_not_found_or_not_editable',
                    'Folder was not found or is not editable by this caller.',
                    status.HTTP_400_BAD_REQUEST,
                )
            raise
        data = serializer.validated_data
        folder = Folder.objects.get(id=data['folder']) if data.get('folder') else None
        upload_id = secrets.token_hex(16)
        temp_path = create_staging_path(request.user.id, upload_id)
        session = ResumableUpload.objects.create(
            owner_id=request.user.id,
            folder=folder,
            name=data['name'],
            size=data['size'],
            mime_type=data.get('mime_type', ''),
            checksum_sha256=data.get('checksum_sha256', ''),
            chunk_size=data.get('chunk_size') or 5 * 1024 * 1024,
            temp_path=temp_path,
            expires_at=timezone.now() + timedelta(days=1),
        )
        return Response(ResumableUploadSerializer(session).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        return Response(ResumableUploadSerializer(self._get_session(request, pk)).data)

    @action(detail=True, methods=['put'], parser_classes=[OctetStreamParser, MultiPartParser, FormParser])
    def chunk(self, request, pk=None):
        return self.chunks(request, pk=pk)

    def chunks(self, request, pk=None):
        body, body_size = self._read_upload_body(request)
        if body_size == 0:
            return self._error('empty_chunk', 'Chunk body is required.', status.HTTP_400_BAD_REQUEST)
        parsed_range = self._parse_content_range(request, body_size)
        if parsed_range is None:
            return self._error('invalid_content_range', 'Content-Range or Upload-Offset is required.', status.HTTP_400_BAD_REQUEST)
        start, end, total = parsed_range
        if end < start or end - start + 1 != body_size:
            return self._error(
                'invalid_content_range',
                'Content-Range byte span must match the request body length.',
                status.HTTP_400_BAD_REQUEST,
            )
        chunk_digest = request.headers.get('X-Drive-Chunk-SHA256')
        if chunk_digest:
            actual_chunk_digest = hashlib.sha256(body).hexdigest()
            if chunk_digest.lower() != actual_chunk_digest:
                return self._error(
                    'chunk_checksum_mismatch',
                    'Chunk bytes did not match expected SHA-256.',
                    status.HTTP_409_CONFLICT,
                    details={'expected_sha256': chunk_digest.lower(), 'actual_sha256': actual_chunk_digest},
                )
        with transaction.atomic():
            session = ResumableUpload.objects.select_for_update().get(id=pk, owner_id=request.user.id)
            if session.status != ResumableUpload.Status.ACTIVE or session.is_expired:
                if session.is_expired and session.status == ResumableUpload.Status.ACTIVE:
                    session.status = ResumableUpload.Status.ABORTED
                    session.save(update_fields=['status', 'updated_at'])
                return self._error('upload_session_expired', 'Upload session is not active.', status.HTTP_404_NOT_FOUND)
            if total is not None and total != session.size:
                return self._error(
                    'upload_size_mismatch',
                    'Content-Range total does not match the upload session size.',
                    status.HTTP_409_CONFLICT,
                    details={'expected_size': session.size, 'actual_size': total},
                )
            if start < session.uploaded_bytes and end < session.uploaded_bytes:
                existing = read_staged_range(session.temp_path, start, body_size)
                if existing == body:
                    return Response({
                        'committed_offset': session.uploaded_bytes,
                        'uploaded_bytes': session.uploaded_bytes,
                        'complete': session.uploaded_bytes == session.size,
                    })
                return self._error(
                    'upload_offset_conflict',
                    'Chunk overlaps already committed bytes with different content.',
                    status.HTTP_409_CONFLICT,
                    details={'committed_offset': session.uploaded_bytes, 'expected_offset': session.uploaded_bytes},
                )
            if start != session.uploaded_bytes:
                return self._error(
                    'upload_offset_conflict',
                    'Chunk start must equal the current committed offset.',
                    status.HTTP_409_CONFLICT,
                    details={'committed_offset': session.uploaded_bytes, 'expected_offset': session.uploaded_bytes},
                    extra={'expected_offset': session.uploaded_bytes},
                )
            if session.uploaded_bytes + body_size > session.size:
                return self._error('chunk_exceeds_size', 'Chunk exceeds declared upload size.', status.HTTP_400_BAD_REQUEST)
            write_chunk(session.temp_path, start, body)
            session.uploaded_bytes += body_size
            session.save(update_fields=['uploaded_bytes', 'updated_at'])
        return Response({
            'committed_offset': session.uploaded_bytes,
            'uploaded_bytes': session.uploaded_bytes,
            'complete': session.uploaded_bytes == session.size,
        })

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        return self.commit(request, pk=pk)

    def commit(self, request, pk=None):
        with transaction.atomic():
            session = ResumableUpload.objects.select_for_update().get(id=pk, owner_id=request.user.id)
            if session.status == ResumableUpload.Status.COMPLETED and session.file_id:
                return Response(FileSerializer(session.file, context={'request': request}).data, status=status.HTTP_200_OK)
            if session.status != ResumableUpload.Status.ACTIVE:
                return self._error('upload_session_not_active', 'Upload session is not active.', status.HTTP_409_CONFLICT)
            if session.is_expired:
                session.status = ResumableUpload.Status.ABORTED
                session.save(update_fields=['status', 'updated_at'])
                return self._error('upload_session_expired', 'Upload session expired.', status.HTTP_404_NOT_FOUND)
            if session.uploaded_bytes != session.size:
                return self._error(
                    'incomplete_upload',
                    'Upload is incomplete.',
                    status.HTTP_409_CONFLICT,
                    details={'committed_offset': session.uploaded_bytes, 'size': session.size},
                )
            sha256_hash = hashlib.sha256()
            md5_hash = hashlib.md5(usedforsecurity=False)
            sample = b''
            with tempfile.NamedTemporaryFile() as staged_file:
                for block in iter_staged_blocks(session.temp_path):
                    if len(sample) < 1024:
                        sample += block[:1024 - len(sample)]
                    sha256_hash.update(block)
                    md5_hash.update(block)
                    staged_file.write(block)
                staged_file.flush()
                staged_size = staged_file.tell()
                if staged_size != session.size:
                    return self._error(
                        'staging_size_mismatch',
                        'Resumable staging does not contain the declared upload size.',
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        retryable=True,
                        details={'expected_size': session.size, 'actual_size': staged_size},
                    )
                staged_file.seek(0)
                digest = sha256_hash.hexdigest()
                expected_sha256 = (
                    session.checksum_sha256
                    or request.data.get('expected_sha256')
                    or request.data.get('checksum_sha256')
                    or ''
                ).lower()
                if expected_sha256 and len(expected_sha256) == 64 and expected_sha256 != digest:
                    session.status = ResumableUpload.Status.ABORTED
                    session.save(update_fields=['status', 'updated_at'])
                    try:
                        delete_staging(session.temp_path)
                    except OSError:
                        pass
                    return self._error(
                        'checksum_mismatch',
                        'Uploaded bytes did not match expected SHA-256.',
                        status.HTTP_409_CONFLICT,
                        details={'expected_sha256': expected_sha256, 'actual_sha256': digest},
                    )
                mime_type = session.mime_type or magic.from_buffer(sample, mime=True)
                extension = session.name.rsplit('.', 1)[-1].lower() if '.' in session.name else ''
                # DRV-38 AC1: file row + session completion + audit row are one
                # transaction — a failed audit write must not leave a committed,
                # unaudited file.
                with transaction.atomic():
                    reservation, quota = StorageQuota.reserve_upload(request.user.id, session.size)
                    if reservation is None:
                        return self._error(
                            'storage_quota_exceeded',
                            'Storage quota exceeded.',
                            status.HTTP_400_BAD_REQUEST,
                        )
                    staged_file.seek(0)
                    file_obj = File.objects.create(
                        name=session.name,
                        owner_id=request.user.id,
                        folder=session.folder,
                        file=DjangoFile(staged_file, name=session.name),
                        file_type=_drive_detect_file_type(mime_type),
                        mime_type=mime_type,
                        size=session.size,
                        extension=extension,
                        checksum_md5=md5_hash.hexdigest(),
                        checksum_sha256=digest,
                    )
                    session.status = ResumableUpload.Status.COMPLETED
                    session.file = file_obj
                    session.save(update_fields=['status', 'file', 'updated_at'])
                    reservation.mark_committed()
                    _record_activity_event(
                        request,
                        Activity.EVENT_CREATE,
                        file_obj,
                        {
                            'file_id': str(file_obj.id),
                            'name': file_obj.name,
                            'size': file_obj.size,
                            'checksum': _stored_sha256(file_obj),
                            'resumable': True,
                        },
                    )
        try:
            delete_staging(session.temp_path)
        except OSError:
            pass
        _enqueue_file_processing(file_obj)
        return Response(FileSerializer(file_obj, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def abort(self, request, pk=None):
        session = self._get_session(request, pk)
        if session.status == ResumableUpload.Status.ACTIVE:
            session.status = ResumableUpload.Status.ABORTED
            session.save(update_fields=['status', 'updated_at'])
        try:
            delete_staging(session.temp_path)
        except OSError:
            pass
        return Response(ResumableUploadSerializer(session).data)

    def destroy(self, request, pk=None):
        return self.abort(request, pk=pk)


class FileViewSet(ExplicitOrgMembershipMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing files.

    list: Get all files for the authenticated user
    create: Upload a new file
    retrieve: Get file details
    update: Update file metadata
    destroy: Move file to trash
    """
    permission_classes = [permissions.IsAuthenticated, IsOwner]
    # Browser uploads use multipart, while MCP/API metadata actions (move,
    # rename, copy, star) send application/json. Keeping JSONParser here is
    # required for those actions to reach request.data instead of failing
    # with HTTP 415 before the action handler runs.
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['folder', 'file_type', 'is_starred', 'is_trashed']

    # DRV-13 H-1: file read/mutate actions honor granular shares.
    # Destroy (trash), restore, copy, and star stay OWNER-ONLY.
    share_required_level = {
        'retrieve': SharePermission.VIEW,
        'preview': SharePermission.VIEW,
        'download': SharePermission.VIEW,
        'versions': SharePermission.VIEW,
        'update': SharePermission.EDIT,
        'partial_update': SharePermission.EDIT,
        'move': SharePermission.EDIT,
    }

    def get_permissions(self):
        if getattr(self, 'action', None) == 'purge':
            return [permissions.IsAuthenticated()]
        if getattr(self, 'action', None) in self.share_required_level:
            return [permissions.IsAuthenticated(), RequireSharePermission()]
        return [permissions.IsAuthenticated(), IsOwner()]

    def get_serializer_class(self):
        if self.action == 'list':
            return FileListSerializer
        if self.action == 'create':
            return FileUploadSerializer
        return FileSerializer

    def get_queryset(self):
        user = self.request.user
        # DRV-13: broaden so a grantee's file is FINDABLE for share-honoring mutate
        # actions; RequireSharePermission then enforces the level (INV-1).
        if getattr(self, 'action', None) in self.share_required_level:
            shared_ids = accessible_object_ids(user, File)
            return File.objects.filter(Q(owner_id=user.id) | Q(id__in=shared_ids))

        if getattr(self, 'action', None) == 'purge' and getattr(user, 'is_staff', False):
            return File.objects.all()

        if self.action in ('list', 'root', 'recent', 'starred'):
            # PLAT-8918 (DRV-181): the default listing is OWNERSHIP-scoped —
            # "my files", not "everything my orgs share with me". The previous
            # base (visible_files) conflated org membership with ownership and
            # leaked ~10k foreign items into a caller owning 11. Org/direct-
            # shared items remain discoverable on the explicit shared surfaces
            # (search ?scope=shared, /drive/api/shares/) which route through
            # the same ACL module (DRV-14 org gate, allow_cross_org default-
            # deny). Folder/owner query params intersect leak-free against
            # this owned-only base (foreign folder -> empty, DRV-60 shape).
            trash = _truthy(self.request.query_params.get('trash')) or _truthy(self.request.query_params.get('is_trashed'))
            queryset = File.objects.filter(owner_id=user.id)
            if trash:
                queryset = queryset.filter(is_trashed=trash).filter(
                    Q(restore_until__isnull=True) | Q(restore_until__gt=timezone.now())
                )
            else:
                queryset = queryset.filter(is_trashed=False)
            queryset = apply_authorized_filters(queryset, self.request)
            search = self.request.query_params.get('search')
            if search:
                queryset = apply_name_search(queryset, search)
            return queryset

        return File.objects.filter(owner_id=user.id)

    def get_object(self):
        """Resolve shared detail actions against only the requested object.

        ``get_queryset`` must expand every folder share for collection-style
        visibility.  Detail actions already carry one primary key, so doing
        that expansion before a primary-key lookup turns an O(1) metadata read
        into an O(all shared files) operation.  In production one root share
        meant materializing nearly 20,000 file IDs even for an owner reading
        their own file.

        Preserve the existing security contract: an object with no applicable
        share remains undiscoverable (404), while an object that is findable
        through a lower share level still reaches ``check_object_permissions``
        and returns 403 for an action that requires a higher level.  The latter
        call also keeps the DRV-28 share-access audit trail intact.
        """
        if getattr(self, 'action', None) in self.share_required_level:
            lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
            lookup_value = self.kwargs.get(lookup_url_kwarg)
            if lookup_value is not None:
                try:
                    file_obj = File.objects.select_related('folder').get(
                        **{self.lookup_field: lookup_value}
                    )
                except (File.DoesNotExist, ValueError, TypeError):
                    raise Http404 from None

                if not require_share_access(
                    self.request.user,
                    file_obj,
                    SharePermission.VIEW,
                ):
                    raise Http404

                self.check_object_permissions(self.request, file_obj)
                return file_obj

        return super().get_object()

    @action(detail=False, methods=['get'])
    def root(self, request):
        """Return only files stored directly in My Drive.

        An unfiltered list intentionally remains available to API clients, so
        the browser must use this explicit root collection instead of encoding
        root as an empty ``folder`` query parameter.
        """
        files = self.get_queryset().filter(folder=None, is_trashed=False)
        page = self.paginate_queryset(files)
        if page is not None:
            serializer = FileListSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)
        serializer = FileListSerializer(files, many=True, context={'request': request})
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        # DRV-6/DRV-32: enforce quota before storage writes and use one
        # actionable JSON error shape for upload/write failures.
        from django.conf import settings
        request_id = _request_id(request)
        upload = request.FILES.get('file')
        size = getattr(upload, 'size', 0) or 0
        quota, _ = StorageQuota.objects.get_or_create(owner_id=request.user.id)
        if size and not quota.can_upload(size):
            return Response(
                _upload_error(
                    'quota_exceeded',
                    'This upload would exceed your storage quota.',
                    request_id,
                    requested_bytes=size,
                    used_bytes=quota.total_used_bytes,
                    quota_bytes=quota.quota_bytes,
                    available_bytes=quota.total_remaining_bytes,
                ),
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            response = super().create(request, *args, **kwargs)
        except ValidationError as exc:
            if _validation_has_code(
                exc.detail,
                'folder_not_found_or_not_editable',
            ):
                return Response(
                    _upload_error(
                        'folder_not_found_or_not_editable',
                        'Folder was not found or is not editable by this caller.',
                        request_id,
                    ),
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if isinstance(exc.detail, dict) and 'error' in exc.detail:
                return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
            return Response(
                _upload_error('invalid_upload', _validation_message(exc.detail), request_id),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except APIException as exc:
            return Response(exc.detail, status=exc.status_code)
        except Exception as exc:  # noqa: BLE001 — upload/write failures must be structured, not silent 500s.
            # The file row is not committed (serializer cleanup handles any
            # just-written object). When tests/ATOMIC_REQUESTS wrap the request
            # in an outer atomic block, a storage/DB exception during insert can
            # mark the transaction rollback-only even though we are converting
            # it into a 500 response; clear that flag so callers can still
            # inspect state after the failed upload.
            if transaction.get_connection().in_atomic_block:
                transaction.set_rollback(False)
            logger.exception("Upload failed before commit request_id=%s: %s", request_id, exc)
            return Response(
                _upload_error(
                    'upload_failed',
                    'File upload failed before it could be committed; no partial file was kept.',
                    request_id,
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        # Non-blocking near-quota warning (after the upload landed).
        if response.status_code < 400 and isinstance(response.data, dict):
            quota.refresh_from_db()
            threshold = getattr(settings, 'DRIVE_QUOTA_WARN_THRESHOLD_PERCENT', 90)
            if quota.total_usage_percent >= threshold:
                response.data['quota_warning'] = {
                    'code': 'quota_threshold',
                    'message': (
                        f'Storage is {quota.total_usage_percent:.0f}% full '
                        f'({quota.total_used_human} of {quota.quota_human}).'
                    ),
                    'usage_percent': round(quota.total_usage_percent, 1),
                    'used_bytes': quota.total_used_bytes,
                    'quota_bytes': quota.quota_bytes,
                }
        return response

    def perform_create(self, serializer):
        # DRV-38 AC1: file row + audit row commit or roll back together.
        with transaction.atomic():
            file_obj = serializer.save()
            _record_activity_event(
                self.request,
                Activity.EVENT_CREATE,
                file_obj,
                {
                    'file_id': str(file_obj.id),
                    'name': file_obj.name,
                    'size': file_obj.size,
                    'checksum': _stored_sha256(file_obj),
                },
            )
            # DRV-105: record the ordered create mutation in the change feed.
            from .change_feed import Change, record_change, snapshot_file
            record_change(
                owner_id=file_obj.owner_id,
                resource_type=Change.RESOURCE_FILE,
                resource_id=file_obj.id,
                mutation='create',
                generation=file_obj.version,
                parent_id=file_obj.folder_id,
                name=file_obj.name,
                payload=snapshot_file(file_obj),
            )

        # Trigger async processing (thumbnails, metadata) only after the
        # file+audit pair has committed — a rolled-back file must not be
        # picked up by the processing worker.
        _enqueue_file_processing(file_obj)

    def perform_destroy(self, instance):
        # Soft delete - move to trash (atomic with its audit row, DRV-38 AC1)
        with transaction.atomic():
            instance.move_to_trash(deleted_by=self.request.user.id)
            _record_activity_event(
                self.request,
                Activity.EVENT_DELETE,
                instance,
                {'name': instance.name, 'soft': True, 'hard': False},
            )
            # DRV-105: record the trash mutation in the change feed.
            from .change_feed import Change, record_change, snapshot_file
            record_change(
                owner_id=instance.owner_id,
                resource_type=Change.RESOURCE_FILE,
                resource_id=instance.id,
                mutation='trash',
                generation=instance.version,
                parent_id=instance.folder_id,
                name=instance.name,
                payload=snapshot_file(instance),
            )

    # DRV-25 P1: optimistic concurrency on metadata updates (INV-1).
    # Any PATCH of clobber-capable fields (name, folder) must supply base_version
    # matching the server's current version. Pure idempotent toggles (is_starred)
    # are exempt since they carry no ordering dependency.
    _STARRED_ONLY_FIELDS = frozenset({'is_starred'})
    _CLOBBER_FIELDS = frozenset({'name', 'folder'})

    def update(self, request, *args, **kwargs):
        from django.db import transaction

        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        # DRV-105 conditional-write: If-Match / X-Drive-Generation precondition.
        # A write whose remote generation has advanced returns 412 (never a
        # silent overwrite). The client reconciles via keep-both.
        if_match = request.headers.get('If-Match') or request.headers.get('X-Drive-Generation')
        if if_match is not None:
            try:
                expected = int(str(if_match).strip().strip('"'))
            except (TypeError, ValueError):
                return Response(
                    {'error': 'If-Match must be the current generation (integer)'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if expected != instance.version:
                return Response(
                    {
                        'error': 'Precondition failed: remote generation has advanced',
                        'current_version': instance.version,
                        'conflict': True,
                    },
                    status=status.HTTP_412_PRECONDITION_FAILED,
                )

        request_fields = set(request.data.keys()) - {'base_version'}
        clobber_touched = request_fields & self._CLOBBER_FIELDS

        if clobber_touched:
            base_version = request.data.get('base_version')
            if base_version is None:
                return Response(
                    {
                        'error': 'base_version is required when updating name or folder',
                        'current_version': instance.version,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                base_version = int(base_version)
            except (TypeError, ValueError):
                return Response({'error': 'base_version must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                # Re-read with row lock to guard against concurrent writers.
                locked = File.objects.select_for_update().get(pk=instance.pk)
                if locked.version != base_version:
                    return Response(
                        {
                            'error': 'Conflict: file was modified by another writer',
                            'current_version': locked.version,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                serializer = self.get_serializer(locked, data=request.data, partial=partial)
                serializer.is_valid(raise_exception=True)
                validated = serializer.validated_data
                validated.pop('base_version', None)
                for attr, value in validated.items():
                    setattr(locked, attr, value)
                locked.version = locked.version + 1
                locked.save()
                Activity.objects.create(
                    user_id=request.user.id,
                    file=locked,
                    action='update',
                    details={'name': locked.name, 'version': locked.version},
                )
                _enqueue_file_activity(request, FileActivity.ACTION_EDIT, file_obj=locked)
                # DRV-105: record the update/rename mutation in the change feed.
                from .change_feed import Change, record_change, snapshot_file
                record_change(
                    owner_id=locked.owner_id,
                    resource_type=Change.RESOURCE_FILE,
                    resource_id=locked.id,
                    mutation='rename' if 'name' in validated else 'update',
                    generation=locked.version,
                    parent_id=locked.folder_id,
                    name=locked.name,
                    payload=snapshot_file(locked),
                )
                return Response(FileSerializer(locked, context={'request': request}).data)

        # Pure idempotent fields (is_starred, etc.) — no version check needed.
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        validated.pop('base_version', None)
        self.perform_update(serializer)
        _enqueue_file_activity(request, FileActivity.ACTION_EDIT, file_obj=serializer.instance)
        # DRV-105: record the update mutation in the change feed.
        from .change_feed import Change, record_change, snapshot_file
        record_change(
            owner_id=serializer.instance.owner_id,
            resource_type=Change.RESOURCE_FILE,
            resource_id=serializer.instance.id,
            mutation='update',
            generation=serializer.instance.version,
            parent_id=serializer.instance.folder_id,
            name=serializer.instance.name,
            payload=snapshot_file(serializer.instance),
        )
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        file_obj = self.get_object()
        enqueue_ms = _enqueue_file_activity(request, FileActivity.ACTION_OPEN, file_obj=file_obj)
        serializer = self.get_serializer(file_obj)
        data = serializer.data
        if isinstance(data, dict):
            data['recent_activity'] = {
                'recorded_action': FileActivity.ACTION_OPEN,
                'enqueue_ms': round(enqueue_ms, 3),
                'retention_days': getattr(settings, 'DRIVE_FILE_ACTIVITY_RETENTION_DAYS', 90),
            }
        return Response(data)

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        """Preview metadata endpoint that records preview recency without serving bytes."""
        file_obj = self.get_object()
        enqueue_ms = _enqueue_file_activity(request, FileActivity.ACTION_PREVIEW, file_obj=file_obj)
        data = FileSerializer(file_obj, context={'request': request}).data
        data['recent_activity'] = {
            'recorded_action': FileActivity.ACTION_PREVIEW,
            'enqueue_ms': round(enqueue_ms, 3),
            'retention_days': getattr(settings, 'DRIVE_FILE_ACTIVITY_RETENTION_DAYS', 90),
        }
        return Response(data)

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_version(self, request, pk=None):
        """Upload new file content as the next version (INV-1 / DRV-25 P1).

        Requires `base_version` and a `file` field. Rejects with 409 if stale.
        Snapshots the current file to FileVersion before replacing content.
        """
        from django.db import transaction

        file_obj = self.get_object()
        base_version = request.data.get('base_version')
        new_file = request.FILES.get('file')

        if base_version is None:
            return Response({'error': 'base_version is required'}, status=status.HTTP_400_BAD_REQUEST)
        if new_file is None:
            return Response({'error': 'file is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            base_version = int(base_version)
        except (TypeError, ValueError):
            return Response({'error': 'base_version must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

        md5_hash = hashlib.md5(usedforsecurity=False)
        sha256_hash = hashlib.sha256()
        try:
            for chunk in new_file.chunks():
                md5_hash.update(chunk)
                sha256_hash.update(chunk)
            new_file.seek(0)
        except OSError as exc:
            return Response(
                {
                    'error': 'upload_read_failed',
                    'message': 'Unable to read uploaded file stream; version was not committed.',
                    'retryable': True,
                    'detail': str(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        new_md5 = md5_hash.hexdigest()
        new_sha256 = sha256_hash.hexdigest()

        with transaction.atomic():
            locked = File.objects.select_for_update().get(pk=file_obj.pk)
            if locked.version != base_version:
                return Response(
                    {
                        'error': 'Conflict: file was modified by another writer',
                        'current_version': locked.version,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            # Snapshot current file content into FileVersion.
            FileVersion.objects.create(
                file=locked,
                version_number=locked.version,
                file_data=locked.file,
                size=locked.size,
                checksum_md5=locked.checksum_md5,
                created_by_id=str(request.user.id),
            )

            # Replace content and advance version.
            locked.file = new_file
            locked.size = new_file.size
            locked.mime_type = new_file.content_type or locked.mime_type
            locked.version = locked.version + 1
            locked.checksum_md5 = new_md5
            locked.checksum_sha256 = new_sha256
            locked.checksum = new_sha256
            locked.checksum_algorithm = 'sha256'
            locked.is_processed = False
            locked.save()

        Activity.objects.create(
            user_id=request.user.id,
            file=locked,
            action='upload_version',
            details={'name': locked.name, 'version': locked.version},
        )
        _enqueue_file_activity(request, FileActivity.ACTION_EDIT, file_obj=locked)
        process_file_upload.delay(str(locked.id))
        return Response(FileSerializer(locked, context={'request': request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """Download file"""
        file_obj = self.get_object()

        if not file_obj.file:
            raise Http404("File not found")

        total_size = file_obj.size
        range_header = request.headers.get('Range')
        range_tuple = _parse_http_range(range_header, total_size) if range_header else None
        if range_header and range_tuple is None:
            response = HttpResponse(status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
            response['Accept-Ranges'] = 'bytes'
            response['Content-Range'] = f'bytes */{total_size}'
            return response

        stored_checksum = _stored_sha256(file_obj)
        checksum_available = bool(stored_checksum)
        if checksum_available:
            try:
                actual_checksum = _file_sha256(file_obj.file)
            except Exception:  # noqa: BLE001 — surface storage/integrity failures before streaming
                logger.exception("download integrity preflight failed for file %s", file_obj.id)
                return Response(
                    {
                        'error': 'integrity_check_failed',
                        'message': 'Could not verify file integrity before download.',
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            if not hmac.compare_digest(actual_checksum, stored_checksum):
                logger.error(
                    "download checksum mismatch for file %s: expected=%s actual=%s",
                    file_obj.id, stored_checksum, actual_checksum,
                )
                return Response(
                    {
                        'error': 'checksum_mismatch',
                        'message': 'Stored file bytes failed checksum verification.',
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        _record_activity_event(
            request,
            Activity.EVENT_DOWNLOAD,
            file_obj,
            {
                'name': file_obj.name,
                'byte_range': list(range_tuple) if range_tuple is not None else None,
                'range': range_header or None,
                'integrity_status': 'partial_range' if range_tuple is not None else (
                    'verified' if checksum_available else 'unverified_legacy'
                ),
            },
        )
        _enqueue_file_activity(request, FileActivity.ACTION_DOWNLOAD, file_obj=file_obj)

        if range_tuple is not None:
            start, end = range_tuple
            length = end - start + 1
            with file_obj.file.open('rb') as fh:
                fh.seek(start)
                body = fh.read(length)
            response = HttpResponse(body, status=status.HTTP_206_PARTIAL_CONTENT, content_type=file_obj.mime_type)
            response['Content-Range'] = f'bytes {start}-{end}/{total_size}'
            response['Content-Length'] = str(length)
            response['Content-Disposition'] = content_disposition_header(True, file_obj.name)
            for header, value in _download_integrity_headers(file_obj, partial=True).items():
                response[header] = value
            return response

        response = FileResponse(
            file_obj.file.open('rb'),
            as_attachment=True,
            filename=file_obj.name
        )
        response['Content-Type'] = file_obj.mime_type
        for header, value in _download_integrity_headers(file_obj, partial=False).items():
            response[header] = value
        return response

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore file from trash"""
        file_obj = self.get_object()
        if not file_obj.is_trashed:
            return Response({'error': 'File is not in trash'}, status=status.HTTP_400_BAD_REQUEST)
        if File.objects.filter(
            owner_id=file_obj.owner_id,
            folder=file_obj.folder,
            name=file_obj.name,
            is_trashed=False,
        ).exclude(pk=file_obj.pk).exists():
            return Response(
                {'error': 'Name collision: an active file with this name already exists in the target folder'},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            # DRV-38 AC1: restore + audit row are one transaction. The except
            # stays OUTSIDE the atomic block so a raised validation error rolls
            # back any partial restore instead of committing it.
            with transaction.atomic():
                file_obj.restore_from_trash()
                _record_activity_event(request, Activity.EVENT_RESTORE, file_obj, {'name': file_obj.name})
                # DRV-105: record the restore mutation in the change feed.
                from .change_feed import Change, record_change, snapshot_file
                record_change(
                    owner_id=file_obj.owner_id,
                    resource_type=Change.RESOURCE_FILE,
                    resource_id=file_obj.id,
                    mutation='restore',
                    generation=file_obj.version,
                    parent_id=file_obj.folder_id,
                    name=file_obj.name,
                    payload=snapshot_file(file_obj),
                )
        except DjangoValidationError as exc:
            return Response(
                {'error': exc.message_dict if hasattr(exc, 'message_dict') else exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(FileSerializer(file_obj).data)

    @action(detail=True, methods=['post', 'delete'])
    def purge(self, request, pk=None):
        """Staff-only privileged hard purge of a trashed file."""
        if not getattr(request.user, 'is_staff', False):
            raise PermissionDenied('Only staff can hard-purge trashed files.')
        file_obj = self.get_object()
        if not file_obj.is_trashed:
            return Response({'error': 'Only trashed files can be purged'}, status=status.HTTP_400_BAD_REQUEST)
        file_id = str(file_obj.id)
        name = file_obj.name
        with transaction.atomic():
            _record_activity_event(
                request,
                Activity.EVENT_DELETE,
                file_obj,
                {'file_id': file_id, 'name': name, 'soft': False, 'hard': True, 'purge': True},
            )
            # DRV-105: record the delete tombstone (GC'd after retention).
            from .change_feed import Change, record_change, snapshot_file
            record_change(
                owner_id=file_obj.owner_id,
                resource_type=Change.RESOURCE_FILE,
                resource_id=file_obj.id,
                mutation='delete',
                generation=file_obj.version,
                parent_id=file_obj.folder_id,
                name=name,
                payload=snapshot_file(file_obj),
                tombstone=True,
            )
            file_obj.delete_permanently()
        return Response({'status': 'purged', 'file_id': file_id})

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        """Move file to a different folder"""
        file_obj = self.get_object()
        from_parent_id = file_obj.folder_id
        folder_id = request.data.get('folder')

        if folder_id:
            try:
                folder = Folder.objects.get(id=folder_id, owner_id=request.user.id)
                file_obj.folder = folder
            except Folder.DoesNotExist:
                return Response(
                    {'error': 'Target folder not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            file_obj.folder = None

        # DRV-38 AC1: move + audit row commit or roll back together.
        with transaction.atomic():
            file_obj.save()
            _record_activity_event(
                request,
                Activity.EVENT_MOVE,
                file_obj,
                {
                    'name': file_obj.name,
                    'from_parent_id': str(from_parent_id) if from_parent_id else None,
                    'to_parent_id': str(folder_id) if folder_id else None,
                },
            )
            # DRV-105: record the move mutation in the change feed.
            from .change_feed import Change, record_change, snapshot_file
            record_change(
                owner_id=file_obj.owner_id,
                resource_type=Change.RESOURCE_FILE,
                resource_id=file_obj.id,
                mutation='move',
                generation=file_obj.version,
                parent_id=file_obj.folder_id,
                name=file_obj.name,
                payload=snapshot_file(file_obj),
            )

        return Response(FileSerializer(file_obj).data)

    @action(detail=True, methods=['post'])
    def copy(self, request, pk=None):
        """Copy file to a folder"""
        file_obj = self.get_object()
        folder_id = request.data.get('folder')

        with transaction.atomic():
            reservation, _ = StorageQuota.reserve_upload(request.user.id, file_obj.size)
            if reservation is None:
                return Response(
                    {'error': 'Storage quota exceeded'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            new_file = File.objects.create(
                name=f"Copy of {file_obj.name}",
                owner_id=request.user.id,
                folder_id=folder_id,
                file=file_obj.file,
                file_type=file_obj.file_type,
                mime_type=file_obj.mime_type,
                size=file_obj.size,
                extension=file_obj.extension,
                checksum_md5=file_obj.checksum_md5,
                checksum_sha256=file_obj.checksum_sha256,
            )
            reservation.mark_committed()
            Activity.objects.create(
                user_id=request.user.id,
                file=new_file,
                action='copy',
                details={'original': str(file_obj.id), 'name': new_file.name}
            )

        return Response(FileSerializer(new_file).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def star(self, request, pk=None):
        """Toggle star status"""
        file_obj = self.get_object()
        file_obj.is_starred = not file_obj.is_starred
        file_obj.save(update_fields=['is_starred'])
        return Response({'is_starred': file_obj.is_starred})

    @action(detail=True, methods=['get'])
    def versions(self, request, pk=None):
        """Get file version history"""
        file_obj = self.get_object()
        versions = file_obj.versions.all()
        serializer = FileVersionSerializer(versions, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def search(self, request):
        """Unified Drive search over authorized files/folders (DRV-58).

        The base querysets always start from Slice-4 ``visible_*`` predicates.
        Filters/ranking/snippets/counts are computed only after that visibility
        boundary so unauthorized owner/folder ids naturally return empty results.
        """
        params = request.query_params
        q = (params.get('q') or params.get('search') or '').strip()
        scope = (params.get('scope') or 'mine').strip().lower()
        entity = (params.get('entity') or 'both').strip().lower()
        sort = (params.get('sort') or ('relevance' if q else 'updated_desc')).strip().lower()
        allowed_sorts = {'relevance', 'updated_desc', 'updated_asc', 'recent', 'name', 'size_asc', 'size_desc'}
        if scope not in {'mine', 'shared', 'all'}:
            return Response({'detail': 'scope must be mine, shared, or all'}, status=status.HTTP_400_BAD_REQUEST)
        if entity not in {'file', 'folder', 'both'}:
            return Response({'detail': 'entity must be file, folder, or both'}, status=status.HTTP_400_BAD_REQUEST)
        if sort not in allowed_sorts:
            return Response({'detail': f'sort must be one of: {", ".join(sorted(allowed_sorts))}'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            page_size = min(max(int(params.get('page_size') or 20), 1), 50)
        except (TypeError, ValueError):
            return Response({'detail': 'page_size must be a positive integer'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cursor = _decode_search_cursor(params.get('cursor'))
            date_from = _parse_iso_datetime_param(params.get('date_from'))
            date_to = _parse_iso_datetime_param(params.get('date_to'))
        except ValidationError as exc:
            return Response({'detail': str(exc.detail[0] if isinstance(exc.detail, list) else exc.detail)}, status=status.HTTP_400_BAD_REQUEST)
        if cursor and cursor.get('sort') != sort:
            return Response({'detail': 'cursor sort does not match request sort'}, status=status.HTTP_400_BAD_REQUEST)

        include_trashed = _parse_bool_param(params.get('trashed'), default=False)
        user_id = str(request.user.id)
        file_qs = visible_files(request.user, include_trashed=include_trashed)
        folder_qs = visible_folders(request.user, include_trashed=include_trashed)
        if scope == 'mine':
            file_qs = file_qs.filter(owner_id=user_id)
            folder_qs = folder_qs.filter(owner_id=user_id)
        elif scope == 'shared':
            file_qs = file_qs.exclude(owner_id=user_id)
            folder_qs = folder_qs.exclude(owner_id=user_id)

        file_qs, folder_qs = self._apply_search_filters(file_qs, folder_qs, request, date_from, date_to)
        # Zero-results signal is based on files matching authorization + filters,
        # before applying q, so callers can explain content-unindexed misses.
        unindexed_count = file_qs.filter(Q(content_text__isnull=True) | Q(content_text='')).count()
        file_qs, folder_qs = self._apply_search_query(file_qs, folder_qs, q)

        if entity == 'file':
            folder_qs = Folder.objects.none().annotate(search_score=Value(0.0, output_field=FloatField()))
        elif entity == 'folder':
            file_qs = File.objects.none().annotate(search_score=Value(0.0, output_field=FloatField()))

        file_qs = self._order_search_queryset(file_qs.select_related('folder'), 'file', sort, request)
        folder_qs = self._order_search_queryset(folder_qs.select_related('parent'), 'folder', sort, request)
        if cursor:
            file_qs = self._apply_search_cursor_queryset(file_qs, 'file', sort, cursor)
            folder_qs = self._apply_search_cursor_queryset(folder_qs, 'folder', sort, cursor)

        # Pull enough from each independently ordered entity stream to merge a
        # correct unified page. Cursor predicates are translated into each
        # queryset before slicing, so page 2+ stays database-bounded instead of
        # materializing every authorized match and filtering in Python.
        per_entity_limit = page_size + 1
        rows = [self._file_search_result(file_obj, request, q) for file_obj in file_qs[:per_entity_limit]]
        rows.extend(self._folder_search_result(folder_obj, request, q) for folder_obj in folder_qs[:per_entity_limit])
        rows.sort(key=lambda row: self._search_sort_key(row, sort))
        page = rows[:page_size]
        next_cursor = None
        if len(rows) > page_size and page:
            next_cursor = _encode_search_cursor({'sort': sort, 'sort_key': list(self._search_sort_key(page[-1], sort))})

        return Response({
            'query': q,
            'scope': scope,
            'entity': entity,
            'sort': sort,
            'page_size': page_size,
            'results': page,
            'next_cursor': next_cursor,
            'has_unindexed_files': unindexed_count > 0,
            'unindexed_count': unindexed_count,
            'total_returned': len(page),
        })

    def _apply_search_filters(self, file_qs, folder_qs, request, date_from, date_to):
        params = request.query_params
        # DRV-60: reject malformed id params with 400 at the boundary instead
        # of letting them 500 at ORM value coercion.
        from .visibility import clean_int_param, clean_uuid_param
        owner_id = clean_int_param(params.get('owner_id'), 'owner_id')
        if owner_id is not None:
            file_qs = file_qs.filter(owner_id=owner_id)
            folder_qs = folder_qs.filter(owner_id=owner_id)
        folder_id = clean_uuid_param(params.get('folder_id') or params.get('folder'), 'folder_id')
        if folder_id:
            file_qs = file_qs.filter(folder_id=folder_id)
            folder_qs = folder_qs.filter(parent_id=folder_id)
        file_type = _list_query_values(params, 'file_type', 'type')
        if file_type:
            file_qs = file_qs.filter(file_type__in=file_type)
            folder_qs = Folder.objects.none()
        mime_values = _list_query_values(params, 'mime', 'mime_type')
        if mime_values:
            mime_q = Q()
            for mime in mime_values:
                if str(mime).endswith('/'):
                    mime_q |= Q(mime_type__startswith=mime)
                else:
                    mime_q |= Q(mime_type=mime)
            file_qs = file_qs.filter(mime_q)
            folder_qs = Folder.objects.none()
        if params.get('starred') not in (None, ''):
            starred = _parse_bool_param(params.get('starred'))
            file_qs = file_qs.filter(is_starred=starred)
            folder_qs = folder_qs.filter(is_starred=starred)
        if date_from:
            field = 'created_at' if params.get('date_field') == 'created_at' else 'updated_at'
            file_qs = file_qs.filter(**{f'{field}__gte': date_from})
            folder_qs = folder_qs.filter(**{f'{field}__gte': date_from})
        if date_to:
            field = 'created_at' if params.get('date_field') == 'created_at' else 'updated_at'
            file_qs = file_qs.filter(**{f'{field}__lte': date_to})
            folder_qs = folder_qs.filter(**{f'{field}__lte': date_to})
        for raw, lookup in (('size_min', 'size__gte'), ('size_max', 'size__lte')):
            if params.get(raw) not in (None, ''):
                try:
                    file_qs = file_qs.filter(**{lookup: int(params[raw])})
                except (TypeError, ValueError):
                    raise ValidationError(f'{raw} must be an integer')
                folder_qs = Folder.objects.none()
        return file_qs, folder_qs

    def _apply_search_query(self, file_qs, folder_qs, term):
        if not term:
            zero_score = Value(0.0, output_field=FloatField())
            return file_qs.annotate(search_score=zero_score), folder_qs.annotate(search_score=zero_score)
        if connection.vendor == 'postgresql':
            search_query = SearchQuery(term, config='simple', search_type='websearch')
            file_qs = (
                file_qs
                .annotate(search_rank=SearchRank('search_vector', search_query),
                          name_similarity=TrigramSimilarity('name', term))
                .annotate(search_score=SearchRank('search_vector', search_query) + TrigramSimilarity('name', term))
                .filter(Q(search_vector=search_query) | Q(name_similarity__gt=0.05) | Q(name__istartswith=term))
            )
            folder_qs = (
                folder_qs
                .annotate(name_similarity=TrigramSimilarity('name', term),
                          description_similarity=TrigramSimilarity('description', term))
                .annotate(search_score=TrigramSimilarity('name', term) + TrigramSimilarity('description', term))
                .filter(
                    Q(name_similarity__gt=0.05)
                    | Q(description_similarity__gt=0.05)
                    | Q(name__istartswith=term)
                    | Q(description__istartswith=term)
                )
            )
            return file_qs, folder_qs
        file_score = (
            Case(When(name__icontains=term, then=Value(2.0)), default=Value(0.0), output_field=FloatField())
            + Case(When(content_text__icontains=term, then=Value(1.0)), default=Value(0.0), output_field=FloatField())
        )
        folder_score = (
            Case(When(name__icontains=term, then=Value(2.0)), default=Value(0.0), output_field=FloatField())
            + Case(When(description__icontains=term, then=Value(1.0)), default=Value(0.0), output_field=FloatField())
        )
        return (
            file_qs.filter(Q(name__icontains=term) | Q(content_text__icontains=term)).annotate(search_score=file_score),
            folder_qs.filter(Q(name__icontains=term) | Q(description__icontains=term)).annotate(search_score=folder_score),
        )

    def _order_search_queryset(self, qs, kind, sort, request):
        if sort == 'name':
            return qs.annotate(sort_name=Lower('name')).order_by('sort_name', 'id')
        if sort == 'updated_asc':
            return qs.order_by('updated_at', 'id')
        if sort in {'updated_desc', 'recent'}:
            if sort == 'recent':
                user_id = str(request.user.id)
                qs = qs.annotate(
                    latest_activity=Max(
                        'recent_activities__created_at',
                        filter=Q(recent_activities__user_id=user_id),
                    )
                ).annotate(sort_recent_at=Coalesce('latest_activity', 'updated_at', 'created_at'))
                return qs.order_by('-sort_recent_at', 'id')
            return qs.order_by('-updated_at', 'id')
        if sort == 'size_asc':
            if kind == 'folder':
                return qs.order_by('id')
            return qs.order_by('size', 'id')
        if sort == 'size_desc':
            if kind == 'folder':
                return qs.order_by('id')
            return qs.order_by('-size', 'id')
        if sort == 'relevance':
            if connection.vendor == 'postgresql':
                return qs.order_by('-search_score', '-updated_at', 'id')
            return qs.order_by('-search_score', '-updated_at', 'id')
        return qs.order_by('-updated_at', 'id')

    def _apply_search_cursor_queryset(self, qs, kind, sort, cursor):
        sort_key = tuple(cursor.get('sort_key') or [])
        if len(sort_key) < 3:
            raise ValidationError('Invalid cursor')
        cursor_kind = str(sort_key[-2])
        cursor_id = str(sort_key[-1])

        def after_primary(primary_after, primary_equal):
            if kind > cursor_kind:
                return qs.filter(primary_after | primary_equal)
            if kind == cursor_kind:
                return qs.filter(primary_after | (primary_equal & Q(id__gt=cursor_id)))
            return qs.filter(primary_after)

        if sort == 'name':
            primary = str(sort_key[0])
            return after_primary(Q(sort_name__gt=primary), Q(sort_name=primary))
        if sort == 'updated_asc':
            primary = parse_datetime(str(sort_key[0]))
            if primary is None:
                raise ValidationError('Invalid cursor')
            return after_primary(Q(updated_at__gt=primary), Q(updated_at=primary))
        if sort == 'updated_desc':
            primary = parse_datetime(self._reverse_string(str(sort_key[0])))
            if primary is None:
                raise ValidationError('Invalid cursor')
            return after_primary(Q(updated_at__lt=primary), Q(updated_at=primary))
        if sort == 'recent':
            primary = parse_datetime(self._reverse_string(str(sort_key[0])))
            if primary is None:
                raise ValidationError('Invalid cursor')
            return after_primary(Q(sort_recent_at__lt=primary), Q(sort_recent_at=primary))
        if sort == 'size_asc':
            try:
                primary = int(sort_key[0])
            except (TypeError, ValueError):
                raise ValidationError('Invalid cursor')
            if kind == 'folder':
                if 0 > primary:
                    return qs
                if 0 == primary and kind >= cursor_kind:
                    return qs.filter(Q(id__gt=cursor_id) if kind == cursor_kind else Q())
                return qs.none()
            return after_primary(Q(size__gt=primary), Q(size=primary))
        if sort == 'size_desc':
            try:
                primary = -int(sort_key[0])
            except (TypeError, ValueError):
                raise ValidationError('Invalid cursor')
            if kind == 'folder':
                if 0 < primary:
                    return qs
                if 0 == primary and kind >= cursor_kind:
                    return qs.filter(Q(id__gt=cursor_id) if kind == cursor_kind else Q())
                return qs.none()
            return after_primary(Q(size__lt=primary), Q(size=primary))
        if sort == 'relevance':
            try:
                primary = -float(sort_key[0])
            except (TypeError, ValueError):
                raise ValidationError('Invalid cursor')
            return after_primary(Q(search_score__lt=primary), Q(search_score=primary))
        raise ValidationError('Invalid cursor')

    def _file_search_result(self, file_obj, request, term):
        name_match = bool(term and term.lower() in (file_obj.name or '').lower())
        content_match = bool(term and term.lower() in (file_obj.content_text or '').lower())
        score = float(getattr(file_obj, 'search_rank', 0) or 0) + float(getattr(file_obj, 'name_similarity', 0) or 0)
        if connection.vendor != 'postgresql' and term:
            score = (2.0 if name_match else 0.0) + (1.0 if content_match else 0.0)
        reason = 'name' if name_match else ('content' if content_match else ('metadata' if term else 'filter'))
        source = file_obj.name if name_match else (file_obj.content_text or file_obj.name)
        return {
            'kind': 'file',
            'id': str(file_obj.id),
            'name': file_obj.name,
            'file_type': file_obj.file_type,
            'mime_type': file_obj.mime_type,
            'size': file_obj.size,
            'folder_id': str(file_obj.folder_id) if file_obj.folder_id else None,
            'owner_id': str(file_obj.owner_id),
            'is_shared': str(file_obj.owner_id) != str(request.user.id),
            'is_trashed': file_obj.is_trashed,
            'snippet': _highlight_snippet(source, term),
            'score': round(score, 6),
            'match_reason': reason,
            'updated_at': file_obj.updated_at.isoformat() if file_obj.updated_at else '',
            'created_at': file_obj.created_at.isoformat() if file_obj.created_at else '',
            'latest_activity': getattr(file_obj, 'latest_activity', None).isoformat() if getattr(file_obj, 'latest_activity', None) else '',
        }

    def _folder_search_result(self, folder_obj, request, term):
        name_match = bool(term and term.lower() in (folder_obj.name or '').lower())
        desc_match = bool(term and term.lower() in (folder_obj.description or '').lower())
        score = float(getattr(folder_obj, 'name_similarity', 0) or 0) + float(getattr(folder_obj, 'description_similarity', 0) or 0)
        if connection.vendor != 'postgresql' and term:
            score = (2.0 if name_match else 0.0) + (1.0 if desc_match else 0.0)
        return {
            'kind': 'folder',
            'id': str(folder_obj.id),
            'name': folder_obj.name,
            'file_type': None,
            'mime_type': None,
            'size': 0,
            'folder_id': str(folder_obj.parent_id) if folder_obj.parent_id else None,
            'owner_id': str(folder_obj.owner_id),
            'is_shared': str(folder_obj.owner_id) != str(request.user.id),
            'is_trashed': folder_obj.is_trashed,
            'snippet': _highlight_snippet(folder_obj.name if name_match else folder_obj.description, term),
            'score': round(score, 6),
            'match_reason': 'folder_name' if name_match else ('folder_description' if desc_match else ('metadata' if term else 'filter')),
            'updated_at': folder_obj.updated_at.isoformat() if folder_obj.updated_at else '',
            'created_at': folder_obj.created_at.isoformat() if folder_obj.created_at else '',
            'latest_activity': getattr(folder_obj, 'latest_activity', None).isoformat() if getattr(folder_obj, 'latest_activity', None) else '',
        }

    def _search_sort_key(self, row, sort):
        updated = row.get('updated_at') or ''
        created = row.get('created_at') or ''
        latest_activity = row.get('latest_activity') or ''
        stable = (row['kind'], row['id'])
        if sort == 'name':
            return ((row.get('name') or '').lower(),) + stable
        if sort == 'updated_asc':
            return (updated,) + stable
        if sort == 'size_asc':
            return (int(row.get('size') or 0),) + stable
        if sort == 'size_desc':
            return (-int(row.get('size') or 0),) + stable
        if sort == 'recent':
            return (self._reverse_string(latest_activity or updated or created),) + stable
        # relevance and updated_desc both use deterministic descending primary.
        if sort == 'updated_desc':
            return (self._reverse_string(updated),) + stable
        return (-float(row.get('score') or 0), self._reverse_string(updated),) + stable

    @staticmethod
    def _reverse_string(value):
        # Gives a stable ascending-comparable representation for descending ISO
        # timestamps without using offset cursors.
        return ''.join(chr(0x10FFFF - ord(ch)) for ch in str(value))

    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Activity-backed Recent with live ACL filtering and 90-day fallback.

        Recent first orders by latest distinct FileActivity for the caller inside
        the retention window. Visible files without retained activity are then
        appended by updated_at so retention expiry never makes files vanish from
        Drive listings; it only changes their recency ordering source.
        """
        raw_limit = request.query_params.get('limit', 20) or 20
        try:
            requested_limit = int(raw_limit)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'limit must be a positive integer'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if requested_limit <= 0:
            return Response(
                {'detail': 'limit must be a positive integer'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = min(requested_limit, 100)
        retention_days = getattr(settings, 'DRIVE_FILE_ACTIVITY_RETENTION_DAYS', 90)
        ordered_ids = [
            row['file_id']
            for row in (
                visible_file_activity(request.user, within_days=retention_days)
                .exclude(file=None)
                .values('file_id')
                .annotate(last_activity=Max('created_at'))
                .order_by('-last_activity', 'file_id')[:limit]
            )
        ]

        # PLAT-8918 (DRV-181): Recent is a default listing leg — owned-only
        # base, matching list/root/starred. Shared items stay on the explicit
        # shared surfaces.
        base_visible = File.objects.filter(owner_id=request.user.id, is_trashed=False)
        active_files = []
        if ordered_ids:
            preserved = {file_id: index for index, file_id in enumerate(ordered_ids)}
            active_files = sorted(base_visible.filter(id__in=ordered_ids), key=lambda f: preserved[f.id])

        remaining = limit - len(active_files)
        fallback_files = []
        if remaining > 0:
            fallback_files = list(
                base_visible.exclude(id__in=ordered_ids)
                .order_by('-updated_at', '-id')[:remaining]
            )

        serializer = FileListSerializer(active_files + fallback_files, many=True, context={'request': request})
        response = Response(serializer.data)
        response['X-Drive-Recent-Activity-Retention-Days'] = str(retention_days)
        response['X-Drive-Recent-Fallback-Order'] = 'updated_at'
        return response

    @action(detail=False, methods=['get'])
    def starred(self, request):
        """Get starred files filtered through the shared visibility predicate."""
        files = self.get_queryset().filter(is_starred=True, is_trashed=False)
        serializer = FileListSerializer(files, many=True, context={'request': request})
        return Response(serializer.data)


class ShareViewSet(ExplicitOrgMembershipMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing shares.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ShareSerializer

    def get_queryset(self):
        user = self.request.user
        # Get shares created by user OR shared with user
        return Share.objects.filter(
            Q(created_by_id=user.id) | user_share_grant_q(user)
        ).filter(is_active=True).order_by('-created_at', '-id')

    def get_serializer_class(self):
        if self.action == 'create':
            return ShareCreateSerializer
        return ShareSerializer

    def create(self, request, *args, **kwargs):
        """Create a share and return the canonical share representation.

        DRV-67: the default ModelViewSet response echoed ShareCreateSerializer
        input fields, so live QA received HTTP 201 without the generated share id
        and could not exercise revoke/filter-at-query-time.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        share = self.perform_create(serializer)
        data = ShareSerializer(share, context=self.get_serializer_context()).data
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        user = self.request.user
        data = serializer.validated_data

        # DRV-11 C-1 (defense in depth): never mint a share for a target the
        # requester does not own. ShareCreateSerializer.validate() already enforces
        # ownership, but perform_create must not rely SOLELY on the serializer —
        # assert it here too so any future caller/refactor stays fail-closed and a
        # public download link can never be created for a non-owned file/folder.
        file_id = data.get('file')
        folder_id = data.get('folder')
        if file_id and not File.objects.filter(id=file_id, owner_id=user.id).exists():
            raise PermissionDenied('You do not own the file you are trying to share.')
        if folder_id and not Folder.objects.filter(id=folder_id, owner_id=user.id).exists():
            raise PermissionDenied('You do not own the folder you are trying to share.')

        # The explicit organization selector is trusted only after membership
        # validation. Multi-org owners must be able to create a grant for the
        # selected tenant instead of silently falling back to a token default.
        # No organization id is ever accepted from the request body.
        selected_org_id = _ensure_explicit_org_membership(self.request)
        owner_org_id = str(
            selected_org_id or getattr(user, 'organization_id', '') or ''
        )

        # DRV-14 INV-4: classify same-org vs cross-org for user/org shares.
        # Link shares are scope-agnostic (anyone with the token can access); no
        # org gate applies.  For user/org shares, the client may supply
        # `grantee_org_id` as a hint (e.g. from a people-picker that resolved
        # the grantee's org).  If the hint differs from the owner's org, the share
        # is cross-org and requires explicit `allow_cross_org=True`; without it
        # the create is denied (cross_org_denied).
        share_type = data['share_type']
        allow_cross_org = bool(data.get('allow_cross_org', False))
        cross_org = False
        if share_type != ShareType.LINK and owner_org_id:
            grantee_org_id = str(data.get('grantee_org_id') or '').strip()
            if grantee_org_id and grantee_org_id != owner_org_id:
                cross_org = True
                if not allow_cross_org:
                    raise PermissionDenied(
                        'Cross-org share requires allow_cross_org=true. (cross_org_denied)'
                    )

        # Create share — organization_id is derived from the owner's validated
        # selected membership (or trusted token default), never from the body.
        # DRV-38 AC1: share row + audit row commit or roll back together.
        with transaction.atomic():
            share = Share.objects.create(
                file_id=data.get('file'),
                folder_id=data.get('folder'),
                share_type=share_type,
                permission=data.get('permission', SharePermission.VIEW),
                shared_with_email=data.get('shared_with_email', ''),
                expires_at=data.get('expires_at'),
                allow_download=data.get('allow_download', True),
                created_by_id=user.id,
                link_token=secrets.token_urlsafe(32) if share_type == ShareType.LINK else None,
                organization_id=owner_org_id or None,
                allow_cross_org=allow_cross_org,
            )

            # Hash password if provided (DRV-12 H-2: salted PBKDF2 via make_password)
            if data.get('password'):
                share.link_password = make_password(data['password'])
                share.save()

            activity_details = {'share_type': share_type, 'permission': share.permission}
            if cross_org:
                # DRV-14 INV-4: distinct audit event for explicitly-allowed cross-org shares.
                activity_details['cross_org'] = True
                activity_details['grantee_org_id'] = str(data.get('grantee_org_id') or '')

            # Log activity
            resource = share.file or share.folder
            _record_activity_event(
                self.request,
                Activity.EVENT_SHARE,
                resource,
                {
                    **activity_details,
                    'share_id': str(share.id),
                    'shared_with_id': share.shared_with_id,
                    'role': share.permission,
                    'link_token': share.link_token,
                },
            )

        return share

    def perform_destroy(self, instance):
        # DRV-13 M-4: get_queryset also surfaces shares the user is a GRANTEE of
        # (shared_with_id), so the default DRF destroy would let a grantee
        # hard-delete a share they don't own. Enforce object-owner here, and
        # SOFT-revoke (is_active=False + revoked_at) instead of hard delete so any
        # referencing audit row (ShareAccessLog, P5) survives (INV-5).
        if str(instance.created_by_id) != str(self.request.user.id):
            raise PermissionDenied('Only the share owner can revoke this share.')
        if instance.is_active:
            # DRV-38 AC1: soft-revoke + its audit row are one transaction.
            with transaction.atomic():
                instance.is_active = False
                instance.revoked_at = timezone.now()
                instance.save(update_fields=['is_active', 'revoked_at', 'updated_at'])
                Activity.objects.create(
                    user_id=self.request.user.id,
                    file=instance.file,
                    folder=instance.folder,
                    action='unshare',
                    details={'share_id': str(instance.id), 'soft_revoke': True},
                )

    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """Revoke a share"""
        share = self.get_object()

        # Only owner can revoke
        if str(share.created_by_id) != str(request.user.id):
            return Response(
                {'error': 'Only the share owner can revoke'},
                status=status.HTTP_403_FORBIDDEN
            )

        share.is_active = False
        share.revoked_at = timezone.now()  # DRV-13 M-4: record soft-revoke time
        share.save(update_fields=['is_active', 'revoked_at', 'updated_at'])

        Activity.objects.create(
            user_id=request.user.id,
            file=share.file,
            folder=share.folder,
            action='unshare',
            details={'share_id': str(share.id)}
        )

        return Response({'status': 'revoked'})

    @action(detail=True, methods=['get'], url_path='access-log')
    def access_log(self, request, pk=None):
        """Return the per-access audit log for a share. Owner only (DRV-28 / HLD §4.4)."""
        try:
            share = Share.objects.get(pk=pk, is_active=True)
        except (Share.DoesNotExist, ValueError):
            raise Http404('Share not found')
        if str(share.created_by_id) != str(request.user.id):
            return Response({'error': 'Only the share owner can view the access log'},
                            status=status.HTTP_403_FORBIDDEN)
        logs = ShareAccessLog.objects.filter(share=share).order_by('-accessed_at')
        page = self.paginate_queryset(logs)
        if page is not None:
            serializer = ShareAccessLogSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = ShareAccessLogSerializer(logs, many=True)
        return Response(serializer.data)


def _log_share_access(request, share, action, result, viewer_id=None):
    """Write one ShareAccessLog row (DRV-28 / HLD §4.4). Never raises.

    Uses _share_client_ip (REMOTE_ADDR-first, DRV-12 P1-A) for IP extraction."""
    try:
        ShareAccessLog.objects.create(
            share=share,
            share_id_copy=share.id,
            viewer_id=viewer_id,
            viewer_ip=_share_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:1024],
            action=action,
            result=result,
        )
    except Exception:
        logger.exception('ShareAccessLog write failed for share %s', share.id)


# ── DRV-12: link-share password hardening (H-2 hashing, M-3 brute-force) ──
_SHARE_PW_MAX_FAILURES = int(os.environ.get('SHARE_PW_MAX_FAILURES', '5'))
_SHARE_PW_WINDOW_SECONDS = int(os.environ.get('SHARE_PW_WINDOW_SECONDS', '900'))  # 15 min


def _share_client_ip(request):
    # DRV-12 P1-A (kei review): X-Forwarded-For is client-controlled — trusting the
    # LEFTMOST value lets an attacker rotate a spoofed IP per request to dodge the
    # (token, ip) rate-limit key, defeating M-3. Key on REMOTE_ADDR (the real TCP
    # peer set by the ingress/proxy — not client-spoofable). Fall back to the
    # RIGHTMOST XFF hop (appended by the trusted last-hop proxy) only if REMOTE_ADDR
    # is unavailable; never trust the leftmost (client-supplied) hop.
    remote = (request.META.get('REMOTE_ADDR') or '').strip()
    if remote:
        return remote
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[-1].strip()
    return 'unknown'


def _share_pw_rl_key(token, request):
    return f'drv_share_pw_fail:{token}:{_share_client_ip(request)}'


def _share_pw_rate_limited(token, request):
    try:
        return (cache.get(_share_pw_rl_key(token, request)) or 0) >= _SHARE_PW_MAX_FAILURES
    except Exception:
        return False  # fail-open on cache outage: never lock out a legitimate user


def _share_pw_record_failure(token, request):
    key = _share_pw_rl_key(token, request)
    try:
        # add() seeds the window on the first failure; incr() bumps within it.
        if cache.add(key, 1, _SHARE_PW_WINDOW_SECONDS) is False:
            try:
                cache.incr(key)
            except ValueError:
                # DRV-12 P2-A (kei review): incr() only raises here if the key is
                # already gone — i.e. the window expired between add() and incr().
                # That means a NEW window has started, so re-seeding at 1 is the
                # correct reset, not a mid-window count loss (incr never fails while
                # the window is live). The race is therefore benign: it cannot drop an
                # in-progress window below threshold; worst case it delays a block by
                # one window boundary. A fully atomic counter would need a Redis
                # INCR+EXPIRE Lua script — not warranted for a 5-per-15min guard.
                cache.set(key, 1, _SHARE_PW_WINDOW_SECONDS)
    except Exception:
        logger.warning('DRV-12: share-password rate-limit cache unavailable', exc_info=True)


def _share_pw_clear_failures(token, request):
    try:
        cache.delete(_share_pw_rl_key(token, request))
    except Exception:
        pass


def _verify_share_password(share, raw):
    """DRV-12 H-2: constant-time verify with transparent legacy upgrade-on-verify.

    Legacy values are bare sha256 hex (no algorithm '$' prefix, pre-DRV-12); on a
    correct legacy match the hash is transparently re-stored via make_password so
    live links keep working without a forced rotation (HLD §7 Q3).
    """
    stored = share.link_password or ''
    if not stored:
        return True
    raw = raw or ''
    if '$' not in stored:
        legacy = hashlib.sha256(raw.encode()).hexdigest()
        if hmac.compare_digest(legacy, stored):
            # DRV-12 P3 (kei review): upgrade-on-verify save() runs outside any view
            # transaction — benign: a duplicate concurrent upgrade just re-writes an
            # equivalent make_password hash (idempotent), and verification has already
            # succeeded, so a lost write only defers the rehash to the next verify.
            share.link_password = make_password(raw)
            share.save(update_fields=['link_password'])
            return True
        return False
    return check_password(raw, stored)


class PublicShareView(APIView):
    """
    View for accessing publicly shared files/folders.
    No authentication required.
    """
    permission_classes = [permissions.AllowAny]

    def _details_response(self, share, request):
        share.record_access()
        _log_share_access(request, share, ShareAccessLog.ACTION_VIEW, ShareAccessLog.RESULT_OK)
        if share.file:
            return Response({
                'type': 'file',
                'name': share.file.name,
                'size': share.file.size,
                'size_human': share.file.size_human,
                'mime_type': share.file.mime_type,
                'allow_download': share.allow_download,
            })
        elif share.folder:
            files = share.folder.files.filter(is_trashed=False)
            return Response({
                'type': 'folder',
                'name': share.folder.name,
                'files': FileListSerializer(files, many=True, context={'request': request}).data,
                'allow_download': share.allow_download,
            })
        return Response({'error': 'Invalid share'}, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request, token):
        """Shared item details.

        DRV-12 M-2: a GET NEVER reads the password (query params leak to access
        logs / browser history / Referer). A password-protected share returns
        `password_required`; the client then POSTs the password (see post()).
        """
        try:
            share = Share.objects.get(link_token=token, share_type=ShareType.LINK, is_active=True)
        except Share.DoesNotExist:
            return Response({'error': 'Share not found or expired'}, status=status.HTTP_404_NOT_FOUND)

        if share.is_expired:
            _log_share_access(request, share, ShareAccessLog.ACTION_VIEW, ShareAccessLog.RESULT_EXPIRED)
            return Response({'error': 'Share has expired'}, status=status.HTTP_410_GONE)

        if share.link_password:
            _log_share_access(request, share, ShareAccessLog.ACTION_VIEW, ShareAccessLog.RESULT_BAD_PASSWORD)
            return Response(
                {'error': 'Password required', 'password_required': True},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return self._details_response(share, request)

    def post(self, request, token):
        """Verify the link password (POST body only) then return details, or download
        the file when `action == 'download'`. DRV-12: H-2 hashed verify + M-3 throttle."""
        try:
            share = Share.objects.get(link_token=token, share_type=ShareType.LINK, is_active=True)
        except Share.DoesNotExist:
            raise Http404("Share not found")

        if share.is_expired:
            _log_share_access(request, share, ShareAccessLog.ACTION_DOWNLOAD, ShareAccessLog.RESULT_EXPIRED)
            return Response({'error': 'Share has expired'}, status=status.HTTP_410_GONE)

        if share.link_password:
            # DRV-12 M-3: brute-force throttle per (token, client-ip) BEFORE verifying.
            if _share_pw_rate_limited(token, request):
                return Response(
                    {'error': 'Too many password attempts — try again later', 'rate_limited': True},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            password = request.data.get('password')
            if not password:
                return Response(
                    {'error': 'Password required', 'password_required': True},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            # DRV-12 H-2: constant-time verify (check_password / legacy upgrade-on-verify).
            if not _verify_share_password(share, password):
                _share_pw_record_failure(token, request)
                _log_share_access(request, share, ShareAccessLog.ACTION_PASSWORD_FAIL, ShareAccessLog.RESULT_BAD_PASSWORD)
                return Response({'error': 'Invalid password'}, status=status.HTTP_401_UNAUTHORIZED)
            _share_pw_clear_failures(token, request)

        # Explicit download (back-compat: a bare POST historically downloaded, but the
        # frontend downloads via download_url; the unlock flow uses action='unlock').
        if request.data.get('action') == 'download':
            if not share.allow_download:
                _log_share_access(request, share, ShareAccessLog.ACTION_DOWNLOAD, ShareAccessLog.RESULT_DENIED)
                return Response({'error': 'Downloads not allowed'}, status=status.HTTP_403_FORBIDDEN)
            if share.file:
                share.record_access()
                _log_share_access(request, share, ShareAccessLog.ACTION_DOWNLOAD, ShareAccessLog.RESULT_OK)
                response = FileResponse(
                    share.file.file.open('rb'), as_attachment=True, filename=share.file.name,
                )
                response['Content-Type'] = share.file.mime_type
                return response
            return Response({'error': 'No file to download'}, status=status.HTTP_400_BAD_REQUEST)

        # DRV-12 P3 (kei review): a verified unlock returns metadata (name/size/mime)
        # even when allow_download=False — this is INTENTIONAL preview-only access per
        # the HLD (allow_download gates the bytes, not the listing). The actual file
        # bytes stay gated above (download branch enforces allow_download).
        return self._details_response(share, request)


class StorageQuotaView(ExplicitOrgMembershipMixin, APIView):
    """
    View for storage quota information.
    Includes tier info and soft/hard limit warnings.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Get current user's storage quota with tier info and warnings"""
        quota, created = StorageQuota.objects.get_or_create(owner_id=request.user.id)
        quota._sync_tier_quota()

        # Get user tier
        user_tier = UserTier.objects.filter(owner_id=request.user.id).first()
        tier_data = None
        warnings = []

        if user_tier:
            tier_data = UserTierSerializer(user_tier).data
            if user_tier.is_over_hard_limit:
                warnings.append({
                    'level': 'hard_limit',
                    'message': 'Storage quota exceeded. Uploads are blocked.',
                    'usage_percent': user_tier.usage_percent,
                })
            elif user_tier.is_over_soft_limit:
                warnings.append({
                    'level': 'soft_limit',
                    'message': 'You have used 90% or more of your storage quota. Consider upgrading.',
                    'usage_percent': user_tier.usage_percent,
                })
        else:
            # Default to free tier limits for warning calculation
            free_tier = StorageTier.objects.filter(slug='free', is_active=True).first()
            if free_tier:
                tier_data = {
                    'tier_name': free_tier.name,
                    'tier_slug': free_tier.slug,
                    'quota_bytes': free_tier.quota_bytes,
                    'soft_limit_bytes': free_tier.soft_limit_bytes,
                }
                usage_pct = (quota.total_used_bytes / free_tier.quota_bytes) * 100 if free_tier.quota_bytes > 0 else 0
                if usage_pct >= 100:
                    warnings.append({
                        'level': 'hard_limit',
                        'message': 'Storage quota exceeded. Uploads are blocked.',
                        'usage_percent': usage_pct,
                    })
                elif usage_pct >= 90:
                    warnings.append({
                        'level': 'soft_limit',
                        'message': 'You have used 90% or more of your storage quota. Consider upgrading.',
                        'usage_percent': usage_pct,
                    })

        serializer = StorageQuotaSerializer(quota)
        data = serializer.data
        data['tier'] = tier_data
        data['warnings'] = warnings
        return Response(data)


class TrashViewSet(ExplicitOrgMembershipMixin, viewsets.ViewSet):
    """
    ViewSet for trash operations.
    """
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """List all trashed items"""
        user = request.user
        files = File.objects.filter(owner_id=user.id, is_trashed=True).filter(
            Q(restore_until__isnull=True) | Q(restore_until__gt=timezone.now())
        )
        folders = Folder.objects.filter(owner_id=user.id, is_trashed=True)

        return Response({
            'files': FileListSerializer(files, many=True, context={'request': request}).data,
            'folders': FolderListSerializer(folders, many=True).data,
        })

    @action(detail=False, methods=['post'])
    def empty(self, request):
        """Staff-only hard purge of the caller's trash."""
        if not getattr(request.user, 'is_staff', False):
            raise PermissionDenied('Only staff can hard-purge trash before the restore window expires.')
        user = request.user

        # Delete files permanently
        trashed_files = File.objects.filter(owner_id=user.id, is_trashed=True)
        for file in trashed_files:
            file.delete_permanently()

        # Delete folders permanently
        trashed_folders = Folder.objects.filter(owner_id=user.id, is_trashed=True)
        trashed_folders.delete()

        return Response({'status': 'trash emptied'})


class ActivityViewSet(ExplicitOrgMembershipMixin, viewsets.ReadOnlyModelViewSet):
    """Paginated, ACL-scoped Drive activity feed (DRV-37)."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ActivitySerializer

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100

    def _is_drive_admin(self):
        user = self.request.user
        return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))

    def _visible_activity_filter(self):
        if self._is_drive_admin():
            return Q()

        user = self.request.user
        return (
            Q(file_id__in=visible_files(user, include_trashed=True).values('id'))
            | Q(folder_id__in=visible_folders(user, include_trashed=True).values('id'))
        )

    def get_queryset(self):
        return (
            Activity.objects.select_related('file', 'folder')
            .filter(self._visible_activity_filter())
            .order_by('-timestamp', '-id')
        )

    def _parse_limit(self):
        raw = self.request.query_params.get('limit', self.DEFAULT_LIMIT)
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({'limit': 'limit must be an integer'})
        if limit < 1:
            raise ValidationError({'limit': 'limit must be at least 1'})
        return min(limit, self.MAX_LIMIT)

    @staticmethod
    def _parse_uuid_param(name, value):
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            raise ValidationError({name: 'must be a UUID'})

    @staticmethod
    def _parse_datetime_param(name, value):
        parsed = parse_datetime(value)
        if parsed is None:
            raise ValidationError({name: 'must be an ISO-8601 datetime'})
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
        return parsed

    @staticmethod
    def _encode_cursor(activity):
        payload = {
            'timestamp': activity.timestamp.astimezone(dt_timezone.utc).isoformat(),
            'id': str(activity.id),
        }
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(',', ':')).encode('utf-8')
        ).decode('ascii').rstrip('=')

    @staticmethod
    def _decode_cursor(cursor):
        try:
            padded = cursor + ('=' * (-len(cursor) % 4))
            payload = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8'))
            ts = parse_datetime(payload['timestamp'])
            item_id = uuid.UUID(payload['id'])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValidationError({'cursor': 'invalid activity cursor'})
        if ts is None:
            raise ValidationError({'cursor': 'invalid activity cursor timestamp'})
        if timezone.is_naive(ts):
            ts = timezone.make_aware(ts, timezone=dt_timezone.utc)
        return ts, item_id

    def _apply_filters(self, queryset):
        params = self.request.query_params

        resource_id = params.get('resource_id')
        if resource_id:
            queryset = queryset.filter(resource_id=self._parse_uuid_param('resource_id', resource_id))

        actor_id = params.get('actor_id')
        if actor_id:
            queryset = queryset.filter(actor_id=str(actor_id))

        event_type = params.get('event_type')
        if event_type:
            event_types = [part.strip() for part in event_type.split(',') if part.strip()]
            if not event_types:
                raise ValidationError({'event_type': 'must include at least one event type'})
            queryset = queryset.filter(event_type__in=event_types)

        from_value = params.get('from')
        if from_value:
            queryset = queryset.filter(timestamp__gte=self._parse_datetime_param('from', from_value))

        to_value = params.get('to')
        if to_value:
            queryset = queryset.filter(timestamp__lt=self._parse_datetime_param('to', to_value))

        cursor = params.get('cursor')
        if cursor:
            cursor_ts, cursor_id = self._decode_cursor(cursor)
            queryset = queryset.filter(Q(timestamp__lt=cursor_ts) | Q(timestamp=cursor_ts, id__lt=cursor_id))

        return queryset

    def list(self, request, *args, **kwargs):
        limit = self._parse_limit()
        queryset = self._apply_filters(self.get_queryset())
        rows = list(queryset[:limit + 1])
        page = rows[:limit]
        next_cursor = self._encode_cursor(page[-1]) if len(rows) > limit and page else None
        return Response({
            'items': self.get_serializer(page, many=True).data,
            'next_cursor': next_cursor,
        })


class DashboardView(ExplicitOrgMembershipMixin, APIView):
    """
    Dashboard view with overview statistics.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        # Get quota
        quota, _ = StorageQuota.objects.get_or_create(owner_id=user.id)
        quota._sync_tier_quota()

        # File stats by type
        file_stats = File.objects.filter(
            owner_id=user.id,
            is_trashed=False
        ).values('file_type').annotate(
            count=models.Count('id'),
            total_size=models.Sum('size')
        )

        # DRV-112: derive the Drive-owned usage from the SAME accounting
        # boundary as the file-type breakdown (the live non-trashed File rows)
        # so the overview never understates the visible file-type totals. The
        # stored quota.used_bytes can drift (incremental add/remove vs. direct
        # writes); the live recompute is authoritative for the dashboard and
        # keeps quota.by_service.drive coherent with the file_stats.
        drive_usage = File.objects.filter(
            owner_id=user.id,
            is_trashed=False
        ).aggregate(
            total_size=models.Sum('size'),
            file_count=models.Count('id'),
        )
        quota.used_bytes = drive_usage['total_size'] or 0
        quota.file_count = drive_usage['file_count'] or 0

        # Recent files
        recent_files = File.objects.filter(
            owner_id=user.id,
            is_trashed=False
        ).order_by('-updated_at')[:5]

        # Recent activity
        recent_activity = Activity.objects.filter(user_id=user.id)[:10]

        return Response({
            'quota': StorageQuotaSerializer(quota).data,
            'file_stats': list(file_stats),
            'recent_files': FileListSerializer(recent_files, many=True, context={'request': request}).data,
            'recent_activity': ActivitySerializer(recent_activity, many=True).data,
        })


class InternalStorageReportView(ExplicitOrgMembershipMixin, APIView):
    """
    Internal API: Accept storage usage reports from other services.
    POST /api/internal/storage/report/
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = StorageReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        owner_id = str(request.user.id)
        with transaction.atomic():
            StorageQuota.objects.get_or_create(owner_id=owner_id)
            quota = StorageQuota.objects.select_for_update().get(owner_id=owner_id)
            prior = ServiceStorageUsage.objects.filter(
                owner_id=owner_id,
                service=data['service'],
            ).first()
            increase = max(0, data['used_bytes'] - (prior.used_bytes if prior else 0))

            reservation = None
            if increase and not data.get('reservation_id'):
                return Response(
                    {
                        'error': 'storage_reservation_required',
                        'detail': (
                            'Increasing reported storage requires a reservation '
                            'from /api/internal/storage/can-upload/.'
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if data.get('reservation_id'):
                reservation = StorageQuotaReservation.objects.select_for_update().filter(
                    id=data['reservation_id'],
                    owner_id=owner_id,
                    status=StorageQuotaReservation.Status.ACTIVE,
                    expires_at__gt=timezone.now(),
                ).first()
                if reservation is None or reservation.size_bytes < increase:
                    return Response(
                        {'error': 'Invalid, expired, or undersized storage reservation'},
                        status=status.HTTP_409_CONFLICT,
                    )

            usage, created = ServiceStorageUsage.objects.update_or_create(
                owner_id=owner_id,
                service=data['service'],
                defaults={
                    'used_bytes': data['used_bytes'],
                    'file_count': data['file_count'],
                }
            )
            if reservation:
                reservation.mark_committed()

        return Response({
            'service': usage.service,
            'used_bytes': usage.used_bytes,
            'file_count': usage.file_count,
            'total_used': quota.total_used_bytes,
            'quota_bytes': quota.quota_bytes,
            'remaining_bytes': quota.total_remaining_bytes,
        })


class InternalCanUploadView(ExplicitOrgMembershipMixin, APIView):
    """
    Internal API: Check if a user has enough quota for an upload.
    POST /api/internal/storage/can-upload/
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = CanUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        file_size = serializer.validated_data['file_size']
        reservation, quota = StorageQuota.reserve_upload(
            request.user.id,
            file_size,
            ttl_seconds=serializer.validated_data['reservation_ttl_seconds'],
        )

        return Response({
            'allowed': reservation is not None,
            'reservation_id': str(reservation.id) if reservation else None,
            'reservation_expires_at': reservation.expires_at if reservation else None,
            'remaining_bytes': quota.total_remaining_bytes,
            'total_used': quota.total_used_bytes,
            'quota_bytes': quota.quota_bytes,
        })


class StorageTierViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing available storage tiers.
    Public read-only — users can see available plans.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StorageTierSerializer
    queryset = StorageTier.objects.filter(is_active=True)
    lookup_field = 'slug'


class UserTierView(APIView):
    """
    View for managing the current user's storage tier.
    GET: Get current tier info
    POST: Schedule a tier change for next billing cycle
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Get current user's tier assignment"""
        user_tier = UserTier.objects.filter(owner_id=request.user.id).first()
        if not user_tier:
            # Return default free tier info
            free_tier = StorageTier.objects.filter(slug='free', is_active=True).first()
            if free_tier:
                return Response({
                    'tier': StorageTierSerializer(free_tier).data,
                    'quota_bytes': free_tier.quota_bytes,
                    'soft_limit_bytes': free_tier.soft_limit_bytes,
                    'pending_change': None,
                })
            return Response({'detail': 'No tier configured'}, status=status.HTTP_404_NOT_FOUND)

        serializer = UserTierSerializer(user_tier)
        return Response(serializer.data)

    def post(self, request):
        """Activate Free or record a non-entitling paid-tier intent."""
        serializer = UserTierScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_tier = serializer.validated_data['tier_id']
        free_tier = StorageTier.objects.filter(slug='free', is_active=True).first()
        if free_tier is None:
            return Response(
                {'detail': 'Free storage tier is not configured'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        user_tier, created = UserTier.objects.get_or_create(
            owner_id=request.user.id,
            defaults={
                'tier': free_tier,
                'quota_bytes': free_tier.quota_bytes,
                'billing_cycle': 'monthly',
            }
        )

        if new_tier.slug == 'free' and created:
            return Response({
                'status': 'activated',
                'tier': UserTierSerializer(user_tier).data,
                'message': 'Free tier activated',
            })

        if user_tier.tier_id == new_tier.id and not user_tier.pending_tier_id:
            return Response({
                'status': 'active',
                'tier': UserTierSerializer(user_tier).data,
                'message': f'{new_tier.name} tier is already active',
            })

        user_tier.schedule_change(new_tier, new_tier.quota_bytes)
        if new_tier.price_monthly > 0:
            return Response({
                'status': 'upgrade_intent_recorded',
                'tier': UserTierSerializer(user_tier).data,
                'message': (
                    f'{new_tier.name} upgrade intent recorded; quota will not '
                    'increase until a trusted payment entitlement is received.'
                ),
            }, status=status.HTTP_202_ACCEPTED)

        return Response({
            'status': 'scheduled',
            'tier': UserTierSerializer(user_tier).data,
            'message': f'Change to {new_tier.name} scheduled for next billing cycle',
        })


class TierEnforcementMixin:
    """
    Mixin to enforce hard limit on uploads.
    Applied to file upload views.
    """

    def check_upload_quota(self, request, file_size):
        """Check if user can upload based on tier hard limit"""
        user_tier = UserTier.objects.filter(owner_id=request.user.id).first()
        if user_tier and user_tier.is_over_hard_limit:
            return False
        # Fall back to StorageQuota check
        quota, _ = StorageQuota.objects.get_or_create(owner_id=request.user.id)
        return quota.can_upload(file_size)


# Import models for aggregation
from django.db import models


class DriveTelemetryView(ExplicitOrgMembershipMixin, APIView):
    """GET /api/drive/internal/telemetry/ — DRV-23 free-tier usage telemetry.

    Staff-only app-level metrics so Product can validate Drive demand: active
    external users, storage used (total/avg), quota hits, file/upload volume, and
    storage pressure. (Node / MinIO / CPU / network pressure come from the cluster
    exporters — this is the Drive-app slice.) ``?export=prometheus`` returns the
    Prometheus text exposition for scraping; otherwise JSON.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not (request.user and request.user.is_authenticated and getattr(request.user, 'is_staff', False)):
            return Response({'error': 'Staff access required'}, status=status.HTTP_403_FORBIDDEN)
        from django.db.models import Sum, Avg, F
        quotas = StorageQuota.objects.all()
        agg = quotas.aggregate(
            total_used=Sum('used_bytes'), total_quota=Sum('quota_bytes'),
            avg_used=Avg('used_bytes'), total_files=Sum('file_count'),
        )
        total_used = agg['total_used'] or 0
        total_quota = agg['total_quota'] or 0
        live_files = File.objects.filter(is_trashed=False)
        metrics = {
            'total_users': quotas.count(),
            'active_users': quotas.filter(used_bytes__gt=0).count(),
            'users_at_quota': quotas.filter(used_bytes__gte=F('quota_bytes')).count(),
            'total_used_bytes': total_used,
            'avg_used_bytes': int(agg['avg_used'] or 0),
            'total_quota_bytes': total_quota,
            'storage_pressure_pct': round(100.0 * total_used / total_quota, 2) if total_quota else 0.0,
            'total_quota_file_count': agg['total_files'] or 0,
            'live_file_count': live_files.count(),
            'upload_volume_bytes': live_files.aggregate(s=Sum('size'))['s'] or 0,
            'free_tier_quota_bytes': StorageQuota.FREE_EXTERNAL_QUOTA_BYTES,
        }
        deletion_state = DeletionProcessorState.objects.filter(
            pk=DeletionProcessorState.SINGLETON_PK,
        ).first()
        expired_pending = DeletionRequest.objects.filter(
            status=DeletionRequest.STATUS_PENDING,
            grace_expires_at__lte=timezone.now(),
        ).count()
        metrics.update({
            'offboarding_expired_pending_count': expired_pending,
            'offboarding_processor_last_started_timestamp': int(
                deletion_state.last_started_at.timestamp()
            ) if deletion_state and deletion_state.last_started_at else 0,
            'offboarding_processor_last_success_timestamp': int(
                deletion_state.last_success_at.timestamp()
            ) if deletion_state and deletion_state.last_success_at else 0,
            'offboarding_processor_last_failure_timestamp': int(
                deletion_state.last_failure_at.timestamp()
            ) if deletion_state and deletion_state.last_failure_at else 0,
            'offboarding_processor_consecutive_failures': (
                deletion_state.consecutive_failures if deletion_state else 0
            ),
            'offboarding_processor_alert_delivery_failed': int(bool(
                deletion_state
                and deletion_state.last_alert_status.startswith('failed:')
            )),
        })
        if request.query_params.get('export') == 'prometheus':
            from django.http import HttpResponse
            body = '\n'.join(f'drive_{k} {v}' for k, v in metrics.items()) + '\n'
            return HttpResponse(body, content_type='text/plain; version=0.0.4; charset=utf-8')
        return Response(metrics)


# =============================================================================
# DRV-22 (VEN-49) — free-validation waitlist / early-access
# =============================================================================
class WaitlistView(APIView):
    """/api/drive/waitlist/ — DRV-22 free-validation waitlist.

    POST (public, AllowAny): the /drive landing-page CTA captures early-access
    interest. Idempotent per email (re-submitting updates, never duplicates). NO
    payment — interest only, for demand validation.

    GET (staff only): inspect/export submissions so agents can review demand.
    ``?export=csv`` streams a CSV; ``?status=pending`` filters. AllowAny at the
    class level lets the public POST through; GET enforces is_staff itself.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        if not email or '@' not in email or '.' not in email.split('@')[-1]:
            return Response({'error': 'A valid email is required'}, status=status.HTTP_400_BAD_REQUEST)
        defaults = {
            'name': (request.data.get('name') or '').strip()[:200],
            'interest': (request.data.get('interest') or '').strip()[:2000],
            'source': (request.data.get('source') or 'drive_landing').strip()[:64] or 'drive_landing',
            'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:400],
            'referrer': (request.data.get('referrer') or request.META.get('HTTP_REFERER') or '')[:400],
        }
        entry, created = WaitlistEntry.objects.update_or_create(email=email, defaults=defaults)

        # VEN-150: send confirmation email (fire-and-forget; failures are logged
        # but never block the response — the waitlist entry is already saved).
        try:
            from django.core.mail import send_mail
            from django.template.loader import render_to_string
            name = defaults['name'] or email.split('@')[0]
            subject = "You're on the Shizuha Drive waitlist!"
            html_body = render_to_string('drive/emails/waitlist_confirmation.html', {
                'name': name,
                'email': email,
            })
            text_body = (
                f"Hi {name},\n\n"
                f"You're on the Shizuha Drive waitlist! We'll email your invite when it's ready.\n\n"
                f"Your email: {email}\n\n"
                f"— Shizuha Drive Team"
            )
            send_mail(
                subject,
                text_body,
                None,  # use DEFAULT_FROM_EMAIL
                [email],
                html_message=html_body,
                fail_silently=False,
            )
        except Exception as exc:
            logger.warning("Waitlist confirmation email failed for %s: %s", email, exc)

        return Response(
            {'ok': True, 'already_on_waitlist': not created, 'id': str(entry.id)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def get(self, request):
        if not (request.user and request.user.is_authenticated and getattr(request.user, 'is_staff', False)):
            return Response({'error': 'Staff access required'}, status=status.HTTP_403_FORBIDDEN)
        qs = WaitlistEntry.objects.all()
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        # NB: use ?export=csv (not ?format=csv — DRF reserves `format` for
        # content negotiation and would 404 on an unknown renderer).
        if request.query_params.get('export') == 'csv':
            import csv
            from django.http import HttpResponse
            resp = HttpResponse(content_type='text/csv')
            resp['Content-Disposition'] = 'attachment; filename="drive_waitlist.csv"'
            writer = csv.writer(resp)
            writer.writerow(['email', 'name', 'interest', 'source', 'status', 'created_at'])
            for e in qs.iterator():
                writer.writerow([e.email, e.name, e.interest, e.source, e.status, e.created_at.isoformat()])
            return resp

        data = WaitlistEntrySerializer(qs, many=True).data
        return Response({'count': len(data), 'results': data})


# =============================================================================
# VEN-153 — data export and offboarding
# =============================================================================
class ExportView(APIView):
    """/api/drive/export/ — VEN-153 data export (ZIP download of all files).

    POST (authenticated): generates a ZIP archive of all current files (including
    trash) and every retained version. The archive is staged on disk and each
    object is copied directly into its ZIP member in bounded chunks. A manifest
    records the explicit scope and a digest for every exported blob. Any storage
    read/write failure aborts the request rather than returning a partial archive.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        import tempfile
        import zipfile

        user_id = str(request.user.id)
        files = File.objects.filter(owner_id=user_id).select_related('folder').prefetch_related('versions')
        file_count = files.count()
        manifest_entries = []
        used_archive_names = set()

        def safe_archive_name(raw_name, fallback):
            """Keep exported paths relative and traversal-safe."""
            parts = [
                part for part in str(raw_name or '').replace('\\', '/').split('/')
                if part not in ('', '.', '..')
            ]
            return '/'.join(parts) or fallback

        def unique_archive_name(raw_name, stable_id):
            candidate = safe_archive_name(raw_name, str(stable_id))
            key = candidate.casefold()
            if key in used_archive_names:
                stem, suffix = os.path.splitext(candidate)
                candidate = f'{stem}__{stable_id}{suffix}'
                key = candidate.casefold()
            used_archive_names.add(key)
            return candidate

        def write_blob(zf, field_file, archive_name, *, expected_size=None):
            if not field_file or not field_file.name:
                raise APIException(f"Export failed: missing storage object for '{archive_name}'")
            digest = hashlib.sha256()
            exported_size = 0
            try:
                with field_file.open('rb') as source, zf.open(
                    archive_name, 'w', force_zip64=True,
                ) as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
                        digest.update(chunk)
                        exported_size += len(chunk)
            except Exception as exc:
                logger.exception('export: failed to copy storage object %s', field_file.name)
                raise APIException(
                    f"Export failed: could not read storage object for '{archive_name}'"
                ) from exc
            return {
                'archive_path': archive_name,
                'declared_size': expected_size,
                'exported_size': exported_size,
                'sha256': digest.hexdigest(),
            }

        # NamedTemporaryFile remains open until FileResponse closes it. Django
        # registers its normal close() as a resource closer, which both preserves
        # the response lifecycle and deletes the staged archive automatically.
        tmp = tempfile.NamedTemporaryFile(mode='w+b', suffix='.zip')
        try:
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in files.iterator(chunk_size=100):
                    original_path = safe_archive_name(f.path, f.name or str(f.id))
                    prefix = '.trash' if f.is_trashed else 'files'
                    arcname = unique_archive_name(f'{prefix}/{original_path}', f.id)
                    entry = write_blob(zf, f.file, arcname, expected_size=f.size)
                    entry.update({
                        'kind': 'trashed_file' if f.is_trashed else 'current_file',
                        'file_id': str(f.id),
                        'original_path': original_path,
                    })
                    manifest_entries.append(entry)

                    for version in f.versions.all():
                        version_arcname = unique_archive_name(
                            f'.versions/{f.id}/v{version.version_number}/{f.name}',
                            version.id,
                        )
                        version_entry = write_blob(
                            zf,
                            version.file_data,
                            version_arcname,
                            expected_size=version.size,
                        )
                        version_entry.update({
                            'kind': 'file_version',
                            'file_id': str(f.id),
                            'version_id': str(version.id),
                            'version_number': version.version_number,
                            'original_path': original_path,
                        })
                        manifest_entries.append(version_entry)

                manifest = {
                    'contract_version': 1,
                    'complete': True,
                    'scope': {
                        'current_files': 'included',
                        'trashed_files': 'included under .trash/',
                        'retained_versions': 'included under .versions/',
                    },
                    'file_row_count': file_count,
                    'blob_count': len(manifest_entries),
                    'entries': manifest_entries,
                }
                zf.writestr(
                    'manifest.json',
                    json.dumps(manifest, sort_keys=True, separators=(',', ':')),
                )

            tmp.flush()
            tmp.seek(0)
            resp = FileResponse(
                tmp,
                content_type='application/zip',
                filename='shizuha-drive-export.zip',
                as_attachment=True,
            )
        except Exception:
            tmp.close()
            raise

        try:
            Activity.objects.create(
                user_id=user_id, actor_id=user_id,
                action='data_export', event_type='data_export',
                metadata={'file_count': file_count, 'blob_count': len(manifest_entries)},
                details={'file_count': file_count, 'blob_count': len(manifest_entries)},
            )
        except Exception:
            resp.close()
            raise
        return resp


class OffboardingView(APIView):
    """/api/drive/offboard/ — VEN-153 account offboarding with 30-day grace.

    POST (authenticated): initiate account deletion. Creates a DeletionRequest
    with a 30-day grace period. The user can cancel within that window.

    GET (authenticated): view the user's active deletion request (if any).

    DELETE (authenticated): cancel a pending deletion request.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .serializers import DeletionRequestCreateSerializer

        user_id = str(request.user.id)

        serializer = DeletionRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        grace_days = getattr(settings, 'DRIVE_DELETION_GRACE_DAYS', 30)
        try:
            with transaction.atomic():
                dr = DeletionRequest.objects.create(
                    owner_id=user_id,
                    email=request.user.email or '',
                    reason=serializer.validated_data.get('reason', ''),
                    grace_expires_at=timezone.now() + timedelta(days=grace_days),
                )
        except IntegrityError:
            # The conditional unique constraint is the race arbiter. Catch
            # outside atomic so the connection is usable for the conflict read.
            existing = DeletionRequest.objects.filter(
                owner_id=user_id,
                status=DeletionRequest.STATUS_PENDING,
            ).first()
            return Response(
                {'error': 'You already have a pending deletion request.',
                 'deletion_request_id': str(existing.id) if existing else None},
                status=status.HTTP_409_CONFLICT,
            )

        Activity.objects.create(
            user_id=user_id, actor_id=user_id,
            action='deletion_requested', event_type='deletion_requested',
            metadata={'deletion_request_id': str(dr.id), 'grace_days': grace_days},
            details={'deletion_request_id': str(dr.id), 'grace_days': grace_days},
        )

        from .serializers import DeletionRequestSerializer
        return Response(
            DeletionRequestSerializer(dr).data,
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        user_id = str(request.user.id)
        dr = DeletionRequest.objects.filter(owner_id=user_id).order_by('-requested_at').first()
        if not dr:
            return Response({'deletion_request': None})
        from .serializers import DeletionRequestSerializer
        return Response({'deletion_request': DeletionRequestSerializer(dr).data})

    def delete(self, request):
        user_id = str(request.user.id)
        with transaction.atomic():
            dr = DeletionRequest.objects.select_for_update().filter(
                owner_id=user_id,
                status=DeletionRequest.STATUS_PENDING,
            ).first()
            if not dr:
                return Response(
                    {'error': 'No pending deletion request to cancel.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if dr.grace_expires_at <= timezone.now():
                return Response(
                    {'error': 'The deletion grace period has expired and cancellation is closed.'},
                    status=status.HTTP_409_CONFLICT,
                )
            dr.cancel()
            Activity.objects.create(
                user_id=user_id, actor_id=user_id,
                action='deletion_cancelled', event_type='deletion_cancelled',
                metadata={'deletion_request_id': str(dr.id)},
                details={'deletion_request_id': str(dr.id)},
            )
        return Response({'ok': True, 'status': 'cancelled'})


# ---------------------------------------------------------------------------
# DRV-105: change-feed / delta contract — ordered mutations + server cursor
# ---------------------------------------------------------------------------

class ChangeFeedView(ExplicitOrgMembershipMixin, APIView):
    """GET /api/changes?cursor=<opaque> — ordered mutations + next cursor.

    Returns the owner's mutations with id > cursor in ascending order, plus an
    opaque ``next_cursor``. An empty delta returns ``changes: []`` with the
    same cursor (client-safe fast path). Deletes appear as tombstones in
    ``payload`` and are GC'd after the retention window.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .change_feed import (
            CHANGES_PAGE_SIZE,
            cursor_is_expired,
            decode_cursor,
            encode_cursor,
            get_changes,
        )
        owner_id = str(request.user.id)
        cursor = decode_cursor(request.query_params.get('cursor'))
        if cursor is None:
            return Response(
                {'error': 'invalid cursor'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if cursor_is_expired(owner_id, cursor):
            return Response(
                {
                    'error': 'cursor expired',
                    'cursor_expired': True,
                    'retry': 're-bootstrap via GET /api/changes/bootstrap/',
                },
                status=status.HTTP_410_GONE,
            )
        try:
            limit = int(request.query_params.get('limit', CHANGES_PAGE_SIZE))
        except (TypeError, ValueError):
            limit = CHANGES_PAGE_SIZE
        limit = max(1, min(limit, 1000))

        changes, next_cursor, has_more = get_changes(
            owner_id, cursor=cursor, limit=limit,
        )
        data = [{
            'id': c.id,
            'cursor': encode_cursor(c.id),
            'resource_type': c.resource_type,
            'resource_id': str(c.resource_id),
            'mutation': c.mutation,
            'generation': c.generation,
            'parent_id': str(c.parent_id) if c.parent_id else None,
            'name': c.name,
            'payload': c.payload,
            'created_at': c.created_at.isoformat() if c.created_at else None,
        } for c in changes]
        return Response({
            'changes': data,
            'next_cursor': encode_cursor(next_cursor),
            'has_more': has_more,
        })


class ChangeBootstrapView(ExplicitOrgMembershipMixin, APIView):
    """GET /api/changes/bootstrap — fence cursor + initial snapshot.

    A fresh client acquires the fence (current feed head), lists the tree
    as-of that fence via ``snapshot``, then replays
    ``GET /api/changes?cursor=<fence_cursor>`` strictly after it — the snapshot
    and the replay are disjoint and complete (no gap, no overlap).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .change_feed import (
            bootstrap_snapshot,
            encode_cursor,
            get_fence,
        )
        owner_id = str(request.user.id)
        fence = get_fence(owner_id)
        snapshot = bootstrap_snapshot(owner_id, fence=fence)
        return Response({
            'fence_cursor': encode_cursor(fence),
            'snapshot': snapshot,
        })


class ConflictArtifactView(ExplicitOrgMembershipMixin, APIView):
    """POST /api/changes/conflicts — idempotent conflict-artifact creation.

    Body: {resource_id, conflict_identity, idempotency_key, name, payload}.
    Two concurrent/retried creates with the same conflict identity + idempotency
    key produce exactly one artifact with one stored ``first_detected_at``; the
    replay returns the existing artifact (``created: false``).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .change_feed import create_conflict_artifact
        owner_id = str(request.user.id)
        resource_id = request.data.get('resource_id')
        conflict_identity = (request.data.get('conflict_identity') or '').strip()
        idempotency_key = (request.data.get('idempotency_key') or '').strip()
        name = (request.data.get('name') or '').strip()
        if not resource_id or not conflict_identity or not idempotency_key or not name:
            return Response(
                {'error': 'resource_id, conflict_identity, idempotency_key and name are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            resource_uuid = uuid.UUID(str(resource_id))
        except (ValueError, TypeError):
            return Response(
                {'error': 'resource_id must be a UUID'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        artifact, created = create_conflict_artifact(
            owner_id=owner_id,
            resource_id=resource_uuid,
            conflict_identity=conflict_identity,
            idempotency_key=idempotency_key,
            name=name,
            payload=request.data.get('payload') or {},
        )
        return Response({
            'id': str(artifact.id),
            'name': artifact.name,
            'first_detected_at': artifact.first_detected_at.isoformat(),
            'created': created,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
