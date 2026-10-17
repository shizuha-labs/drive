"""
Permissions for Shizuha Drive

Defines permission classes for controlling access to files and folders.
"""
from django.db.models import Q
from rest_framework import permissions
from .models import Share, SharePermission, ShareType
from .visibility import share_allows_user_org, user_org_ids, user_share_grant_q


def _share_client_ip(request):
    """REMOTE_ADDR-first IP extraction (DRV-12 P1-A: leftmost XFF is client-spoofable)."""
    remote = (request.META.get('REMOTE_ADDR') or '').strip()
    if remote:
        return remote
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[-1].strip()
    return 'unknown'


def is_org_admin_or_owner(user, resource_org_id=None) -> bool:
    """Org admin/owner privilege check — SCOPED to the resource's org (DRV-11 C-2).

    Superusers always pass. Otherwise the user must BOTH (a) hold an org admin/owner
    role from the TRUSTED JWT claim, AND (b) belong to the SAME org as the resource
    (``resource_org_id``, sourced from a verified attribute on the object — never a
    client header). When the resource carries no org id (``None``) — e.g. File /
    Folder, which are owned per-user with no organization column — the org-admin
    bypass does NOT apply (fail closed) and access falls through to the owner/share
    checks.

    Previously this granted access on the role claim ALONE, with no comparison of
    the user's org to the resource's org, so an admin/owner of org A would be
    granted access to org B's resources. The grant is now resource-scoped.
    """
    if getattr(user, 'is_superuser', False):
        return True
    if getattr(user, 'organization_role', None) not in ('owner', 'admin'):
        return False
    if resource_org_id is None:
        return False
    user_org = getattr(user, 'organization_id', None)
    return bool(user_org) and str(user_org) == str(resource_org_id)


class ServiceAccessPermission(permissions.BasePermission):
    SERVICE_SLUG = 'drive'
    message = 'You do not have access to this service.'

    def has_permission(self, request, view):
        user = request.user
        if not hasattr(user, 'enabled_services'):
            return True  # Not a federated user (e.g., Django admin)
        if user.enabled_services is None:
            return True  # Unrestricted (staff/superuser)
        return self.SERVICE_SLUG in user.enabled_services


class IsOwner(permissions.BasePermission):
    """
    Permission that only allows owners to access the object.
    """

    def has_object_permission(self, request, view, obj):
        # Org admins/owners can access a file only within their OWN org (DRV-11 C-2).
        if is_org_admin_or_owner(request.user, getattr(obj, 'organization_id', None)):
            return True

        # Get owner_id from the object
        owner_id = getattr(obj, 'owner_id', None)
        if owner_id is None:
            return False

        # Compare with request user's ID
        user_id = getattr(request.user, 'id', None) or getattr(request.user, 'pk', None)
        return str(owner_id) == str(user_id)


class IsOwnerOrShared(permissions.BasePermission):
    """
    Permission that allows access if user is owner OR has a valid share.
    """

    def has_object_permission(self, request, view, obj):
        # Org admins/owners can access a file only within their OWN org (DRV-11 C-2).
        if is_org_admin_or_owner(request.user, getattr(obj, 'organization_id', None)):
            return True

        user_id = str(getattr(request.user, 'id', None) or getattr(request.user, 'pk', None))

        # Owner always has access
        owner_id = getattr(obj, 'owner_id', None)
        if owner_id and str(owner_id) == user_id:
            return True

        # Check for share permission
        from .models import File, Folder

        if isinstance(obj, File):
            # Check direct file share
            has_share = Share.objects.filter(
                file=obj,
                is_active=True
            ).filter(user_share_grant_q(request.user)).exists()
            if has_share:
                return True

            # Check folder share (inherits to files)
            if obj.folder:
                has_folder_share = Share.objects.filter(
                    folder=obj.folder,
                    is_active=True
                ).filter(user_share_grant_q(request.user)).exists()
                if has_folder_share:
                    return True

        elif isinstance(obj, Folder):
            # Check folder share
            has_share = Share.objects.filter(
                folder=obj,
                is_active=True
            ).filter(user_share_grant_q(request.user)).exists()
            if has_share:
                return True

            # Check parent folder share (inherits)
            parent = obj.parent
            while parent:
                has_parent_share = Share.objects.filter(
                    folder=parent,
                    is_active=True
                ).filter(user_share_grant_q(request.user)).exists()
                if has_parent_share:
                    return True
                parent = parent.parent

        return False


class HasSharePermission(permissions.BasePermission):
    """
    Check specific share permission level (view, comment, edit).
    """

    def __init__(self, required_permission=SharePermission.VIEW):
        self.required_permission = required_permission

    def has_object_permission(self, request, view, obj):
        # Org admins/owners bypass share restrictions only within their OWN org (DRV-11 C-2).
        if is_org_admin_or_owner(request.user, getattr(obj, 'organization_id', None)):
            return True

        user_id = str(getattr(request.user, 'id', None) or getattr(request.user, 'pk', None))

        # Owner always has full permission
        owner_id = getattr(obj, 'owner_id', None)
        if owner_id and str(owner_id) == user_id:
            return True

        # Get the share
        from .models import File, Folder

        shares = None
        if isinstance(obj, File):
            shares = Share.objects.filter(
                file=obj,
                is_active=True
            ).filter(user_share_grant_q(request.user))
        elif isinstance(obj, Folder):
            shares = Share.objects.filter(
                folder=obj,
                is_active=True
            ).filter(user_share_grant_q(request.user))

        if not shares or not shares.exists():
            return False

        # Check permission level
        share = shares.first()
        permission_levels = {
            SharePermission.VIEW: 1,
            SharePermission.COMMENT: 2,
            SharePermission.EDIT: 3,
        }

        share_level = permission_levels.get(share.permission, 0)
        required_level = permission_levels.get(self.required_permission, 0)

        return share_level >= required_level


# ── DRV-13 H-1: granular share-role enforcement (HLD §4.1 / §5 INV-1,5,6) ────
#
# `require_share_access` is the SINGLE precedence helper every write+read
# enforcement point must call (INV-1: no share path may bypass it). Precedence
# (HLD §4.1): owner → EDIT; same-org admin (§4.2) → EDIT; active+unexpired
# user-share → its level; active+unexpired org-share → its level iff the user's
# org matches; else DENY. Shares are re-validated (is_active + expiry + org) at
# REQUEST time (INV-6) — never trusted from a stale read.

_SHARE_RANK = {
    SharePermission.VIEW: 1,
    SharePermission.COMMENT: 2,
    SharePermission.EDIT: 3,
}


def _share_rank(permission) -> int:
    return _SHARE_RANK.get(permission, 0)


def _user_id_str(user) -> str:
    return str(getattr(user, 'id', None) or getattr(user, 'pk', None))


def _folder_ancestor_ids(folder_id):
    """Return folder_id plus every ancestor id with one recursive query.

    The previous implementation followed ``folder.parent`` in Python and then
    issued one Share query per level. Permission checks sit on the mutate hot
    path, so a deep tree amplified into D+1 share SELECTs. Keep the same
    inheritance semantics, but batch all folder shares through one IN query.
    """
    if not folder_id:
        return []
    from django.db import connection
    from .models import Folder

    folder_id_param = getattr(folder_id, 'hex', str(folder_id).replace('-', ''))
    table = connection.ops.quote_name(Folder._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH RECURSIVE ancestors(id, parent_id) AS (
                SELECT id, parent_id FROM {table} WHERE id = %s
                UNION ALL
                SELECT f.id, f.parent_id
                FROM {table} f
                JOIN ancestors a ON f.id = a.parent_id
            )
            SELECT id FROM ancestors
            """,
            [folder_id_param],
        )
        return [row[0] for row in cursor.fetchall()]


def _active_shares_for(obj):
    """Active, non-expired shares applying to obj — directly and inherited from
    ancestor folders (a folder share cascades to its files/subfolders). Expiry is
    re-checked here at request time (INV-6), not just is_active."""
    from .models import File, Folder
    from django.utils import timezone

    now = timezone.now()
    folder_ids = []
    share_filter = (
        Q(is_active=True)
        & Q(revoked_at__isnull=True)
        & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )
    if isinstance(obj, File):
        target_filter = Q(file=obj)
        folder_ids = _folder_ancestor_ids(obj.folder_id)
    elif isinstance(obj, Folder):
        target_filter = Q(pk__in=[])
        folder_ids = _folder_ancestor_ids(obj.id)
    else:
        return []

    if folder_ids:
        target_filter |= Q(folder_id__in=folder_ids)
    return list(Share.objects.filter(share_filter & target_filter))


def effective_share_level(user, obj) -> int:
    """The user's effective permission rank (0=none .. 3=edit) on obj per §4.1."""
    user_id = _user_id_str(user)

    # Owner → full (EDIT).
    owner_id = getattr(obj, 'owner_id', None)
    if owner_id and str(owner_id) == user_id:
        return _SHARE_RANK[SharePermission.EDIT]

    # Same-org admin/owner (§4.2) → full. (File/Folder carry no org column, so
    # this is fail-closed there per is_org_admin_or_owner; kept for parity/future.)
    if is_org_admin_or_owner(user, getattr(obj, 'organization_id', None)):
        return _SHARE_RANK[SharePermission.EDIT]

    user_orgs = user_org_ids(user)
    best = 0
    for share in _active_shares_for(obj):
        if share_matches_user(share, user):
            best = max(best, _share_rank(share.permission))
        elif (share.share_type == ShareType.ORGANIZATION
              and share.organization_id
              and str(share.organization_id) in user_orgs):
            best = max(best, _share_rank(share.permission))
    return best


def require_share_access(user, obj, level=SharePermission.VIEW) -> bool:
    """True iff the user's effective level on obj is >= the required level.

    THE single share-authz check — wire every enforcement point through this so
    permission logic never drifts (INV-1)."""
    return effective_share_level(user, obj) >= _share_rank(level)


def accessible_object_ids(user, model):
    """IDs of `model` (File/Folder) objects the user has ANY active, unexpired
    share on — direct or inherited from an ancestor folder, by user-share or
    matching org-share. Used to broaden a detail-action queryset so a grantee's
    object is FINDABLE; the object-level `require_share_access` then enforces the
    actual level. Owner-owned objects are unioned in by the caller."""
    from django.utils import timezone
    from .models import File, Folder
    user_id = _user_id_str(user)
    user_orgs = user_org_ids(user)

    share_q = user_share_grant_q(user)
    if user_orgs:
        share_q |= Q(
            share_type=ShareType.ORGANIZATION,
            organization_id__in=user_orgs,
        )
    now = timezone.now()
    active = (Share.objects.filter(share_q, is_active=True, revoked_at__isnull=True)
              .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)))

    shared_folder_ids = set(
        active.exclude(folder=None).values_list('folder_id', flat=True))
    shared_file_ids = set(
        active.exclude(file=None).values_list('file_id', flat=True))

    # Expand shared folders to include their whole subtree (share inheritance).
    subtree_folder_ids = set(shared_folder_ids)
    for fid in shared_folder_ids:
        try:
            folder = Folder.objects.get(id=fid)
        except Folder.DoesNotExist:
            continue
        subtree_folder_ids.update(d.id for d in folder.get_descendants())

    if model is Folder:
        return subtree_folder_ids
    if model is File:
        ids = set(shared_file_ids)
        if subtree_folder_ids:
            ids.update(
                File.objects.filter(folder_id__in=subtree_folder_ids)
                .values_list('id', flat=True))
        return ids
    return set()


def _find_granting_share(user, obj):
    """Return the Share that grants user access to obj, including inherited folder-cascade grants.

    Uses _active_shares_for(obj) so the ancestor walk and request-time is_expired check
    are identical to the grant path (effective_share_level) — no predicate drift between
    the log path and the auth path (DRV-28 / Rei review finding 2026-06-30).
    """
    user_id = _user_id_str(user)
    user_orgs = user_org_ids(user)
    for share in _active_shares_for(obj):
        if share_matches_user(share, user):
            return share
        if (share.organization_id
                and str(share.organization_id) in user_orgs):
            return share
    return None


def share_matches_user(share, user) -> bool:
    user_id = _user_id_str(user)
    if share.shared_with_id and str(share.shared_with_id) == user_id:
        return True

    if share.share_type != ShareType.USER or not share_allows_user_org(share, user):
        return False
    email = str(getattr(user, 'email', '') or '').strip()
    return (
        bool(email)
        and bool(share.shared_with_email)
        and share.shared_with_email.lower() == email.lower()
    )


class RequireSharePermission(permissions.BasePermission):
    """Object permission that honors granular share roles via `require_share_access`.

    The required level per action comes from ``view.share_required_level`` (a dict
    of {action_name: SharePermission}); unmapped actions default to VIEW for safe
    methods and EDIT for unsafe ones. Owner-only actions (delete, share management)
    must NOT use this class — keep them on ``IsOwner``."""

    message = 'You do not have the required share permission for this action.'

    def has_object_permission(self, request, view, obj):
        level_map = getattr(view, 'share_required_level', None) or {}
        level = level_map.get(getattr(view, 'action', None))
        if level is None:
            level = (SharePermission.VIEW
                     if request.method in permissions.SAFE_METHODS
                     else SharePermission.EDIT)
        granted = require_share_access(request.user, obj, level)
        # DRV-28: write ShareAccessLog for authenticated share access (INV-5).
        try:
            from .models import ShareAccessLog
            share = _find_granting_share(request.user, obj)
            if share is not None:
                action = (ShareAccessLog.ACTION_VIEW
                          if request.method in permissions.SAFE_METHODS
                          else ShareAccessLog.ACTION_DOWNLOAD)
                result = ShareAccessLog.RESULT_OK if granted else ShareAccessLog.RESULT_DENIED
                viewer_id = str(getattr(request.user, 'id', '') or '')
                ShareAccessLog.objects.create(
                    share=share,
                    share_id_copy=share.id,
                    viewer_id=viewer_id or None,
                    viewer_ip=_share_client_ip(request),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:1024],
                    action=action,
                    result=result,
                )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                'ShareAccessLog write failed in RequireSharePermission for obj %s', getattr(obj, 'id', obj))
        return granted


class CanUpload(permissions.BasePermission):
    """
    Permission to check if user can upload (has storage quota).
    """

    def has_permission(self, request, view):
        if request.method not in ['POST', 'PUT', 'PATCH']:
            return True

        # Superusers bypass the quota gate. (No resource org here, so the org-admin
        # role alone does not grant a bypass — DRV-11 C-2 fail-closed default.)
        if is_org_admin_or_owner(request.user):
            return True

        # Check content-length header for file uploads
        content_length = request.headers.get('Content-Length')
        if not content_length:
            return True  # Let the view handle validation

        from .models import StorageQuota

        user_id = getattr(request.user, 'id', None) or getattr(request.user, 'pk', None)
        try:
            quota = StorageQuota.objects.get(owner_id=user_id)
            return quota.can_upload(int(content_length))
        except StorageQuota.DoesNotExist:
            # No quota record = use default
            return True
