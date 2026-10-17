"""Shared Drive ACL/visibility predicates (DRV-54 / DRV-4 Slice 4).

All list/search/recent/starred surfaces must route through this module so owner,
direct Share, org Share, folder-share inheritance, trash, expiry, and revocation
semantics cannot drift between endpoints.
"""
from __future__ import annotations

from collections.abc import Iterable

from django.contrib.postgres.search import TrigramSimilarity
from django.db import connection
from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import File, FileActivity, Folder, Share, ShareType


def user_id_str(user) -> str:
    return str(getattr(user, 'id', None) or getattr(user, 'pk', None))


def user_email_str(user) -> str:
    return str(getattr(user, 'email', '') or '').strip()


def user_org_ids(user) -> set[str]:
    values: set[str] = set()
    for attr in ('organization_ids', 'org_ids'):
        raw = getattr(user, attr, None)
        if isinstance(raw, str):
            values.add(raw)
        elif isinstance(raw, Iterable):
            values.update(str(v) for v in raw if v not in (None, ''))
    org = getattr(user, 'organization_id', None)
    if org not in (None, ''):
        values.add(str(org))
    return values


def user_share_org_gate_q(user) -> Q:
    """Read-time org boundary for direct USER shares.

    DRV-14 requires cross-org user grants to be explicit. DRV-65 added
    email-addressed grants for live users whose share rows lack
    ``shared_with_id``; this gate keeps that fallback from bypassing the same
    org / explicit ``allow_cross_org`` invariant when the creator omitted a
    grantee-org hint. Owner-org-less rows remain visible because there is no
    trusted owner org to compare.
    """
    org_gate = (
        Q(allow_cross_org=True)
        | Q(organization_id__isnull=True)
        | Q(organization_id='')
    )
    org_ids = user_org_ids(user)
    if org_ids:
        org_gate |= Q(organization_id__in=org_ids)
    return org_gate


def share_allows_user_org(share, user) -> bool:
    """Python equivalent of :func:`user_share_org_gate_q` for object checks."""
    if getattr(share, 'allow_cross_org', False):
        return True
    owner_org = str(getattr(share, 'organization_id', '') or '').strip()
    if not owner_org:
        return True
    return owner_org in user_org_ids(user)


def user_share_grant_q(user) -> Q:
    """Predicate for active grants addressed to ``user``.

    Existing authenticated share contracts allow any share row with
    ``shared_with_id`` (including LINK rows used by DRV-28 audit enforcement)
    to grant the named user access. DRV-65 adds an email-addressed USER-share
    fallback for live federated users whose share rows lack
    ``shared_with_id``; only that fallback is scoped through the DRV-14 org
    gate so an omitted ``grantee_org_id`` cannot grant cross-org visibility
    unless ``allow_cross_org=True``.
    """
    grant_q = Q(shared_with_id=user_id_str(user))
    email = user_email_str(user)
    if email:
        grant_q |= (
            Q(share_type=ShareType.USER)
            & Q(shared_with_email__iexact=email)
            & user_share_org_gate_q(user)
        )
    return grant_q

def active_share_q(now=None) -> Q:
    now = now or timezone.now()
    return (
        Q(is_active=True)
        & Q(revoked_at__isnull=True)
        & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )


def shares_for_user(user) -> QuerySet:
    grant_q = user_share_grant_q(user)
    org_ids = user_org_ids(user)
    if org_ids:
        grant_q |= Q(share_type=ShareType.ORGANIZATION, organization_id__in=org_ids)
    return Share.objects.filter(active_share_q()).filter(grant_q)


def _descendant_folder_ids(folder_ids: Iterable) -> set:
    roots = [str(v) for v in folder_ids if v]
    if not roots:
        return set()
    if connection.vendor == 'postgresql':
        table = connection.ops.quote_name(Folder._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH RECURSIVE descendants(id) AS (
                    SELECT id FROM {table} WHERE id = ANY(%s::uuid[])
                    UNION ALL
                    SELECT f.id FROM {table} f JOIN descendants d ON f.parent_id = d.id
                )
                SELECT id FROM descendants
                """,
                [roots],
            )
            return {row[0] for row in cursor.fetchall()}

    # Portable sqlite/test fallback; production uses the bounded recursive CTE.
    seen = set(Folder.objects.filter(id__in=roots).values_list('id', flat=True))
    frontier = set(seen)
    while frontier:
        children = set(Folder.objects.filter(parent_id__in=frontier).values_list('id', flat=True)) - seen
        seen.update(children)
        frontier = children
    return seen


def visible_folder_ids(user) -> set:
    shared_roots = shares_for_user(user).exclude(folder=None).values_list('folder_id', flat=True)
    return _descendant_folder_ids(shared_roots)


def visible_file_ids(user) -> set:
    shares = shares_for_user(user)
    ids = set(shares.exclude(file=None).values_list('file_id', flat=True))
    folder_ids = _descendant_folder_ids(shares.exclude(folder=None).values_list('folder_id', flat=True))
    if folder_ids:
        ids.update(File.objects.filter(folder_id__in=folder_ids).values_list('id', flat=True))
    return ids


def visible_files(user, *, include_trashed: bool = False) -> QuerySet:
    qs = File.objects.filter(Q(owner_id=user_id_str(user)) | Q(id__in=visible_file_ids(user)))
    if not include_trashed:
        qs = qs.filter(is_trashed=False)
    return qs.distinct()


def visible_folders(user, *, include_trashed: bool = False) -> QuerySet:
    qs = Folder.objects.filter(Q(owner_id=user_id_str(user)) | Q(id__in=visible_folder_ids(user)))
    if not include_trashed:
        qs = qs.filter(is_trashed=False)
    return qs.distinct()


def clean_uuid_param(value, name):
    """DRV-60: validate a UUID query param at the API boundary.

    A malformed UUID used to flow into the ORM filter and raise at value
    coercion -> Django 500. Raise a DRF ValidationError instead so the client
    gets a clean 400 naming the bad parameter. Returns None for absent/empty.
    """
    if value in (None, ''):
        return None
    import uuid as _uuid
    from rest_framework.exceptions import ValidationError as _DRFValidationError
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise _DRFValidationError({name: 'must be a valid UUID'})


def clean_int_param(value, name):
    """DRV-60: same boundary guard for integer id params (owner_id)."""
    if value in (None, ''):
        return None
    from rest_framework.exceptions import ValidationError as _DRFValidationError
    try:
        return int(str(value))
    except (ValueError, TypeError):
        raise _DRFValidationError({name: 'must be an integer'})


def apply_authorized_filters(qs: QuerySet, request) -> QuerySet:
    """Apply leak-free owner/folder filters after the base visible predicate.

    An unauthorized owner_id/folder_id simply intersects to an empty queryset;
    a MALFORMED one is rejected with 400 at the boundary (DRV-60).
    """
    owner_id = clean_int_param(request.query_params.get('owner_id'), 'owner_id')
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)
    folder_id = clean_uuid_param(
        request.query_params.get('folder_id') or request.query_params.get('folder'),
        'folder_id',
    )
    if folder_id and hasattr(qs.model, 'folder_id'):
        qs = qs.filter(folder_id=folder_id)
    return qs


def apply_name_search(qs: QuerySet, term: str) -> QuerySet:
    term = (term or '').strip()
    if not term:
        return qs
    if connection.vendor == 'postgresql':
        # DRV-48 installed pg_trgm indexes; avoid the production icontains scan.
        return (qs.annotate(name_similarity=TrigramSimilarity('name', term))
                  .filter(Q(name_similarity__gt=0.05) | Q(name__istartswith=term))
                  .order_by('-name_similarity', 'name', 'id'))
    # Non-postgres test/dev fallback only; production hot path above is trigram.
    return qs.filter(name__icontains=term)


def visible_activity(user) -> QuerySet:
    """Activity/recent rows whose referenced file/folder is still visible now."""
    from .models import Activity

    return Activity.objects.filter(user_id=user_id_str(user)).filter(
        Q(file_id__in=visible_files(user).values('id'))
        | Q(folder_id__in=visible_folders(user).values('id'))
    )


def visible_file_activity(user, *, within_days: int | None = None) -> QuerySet:
    """FileActivity rows whose target is visible to the caller right now.

    Revoked/expired shares therefore disappear from Recent at query time while
    the underlying activity rows remain for audit/re-grant continuity.
    """
    qs = FileActivity.objects.filter(user_id=user_id_str(user)).filter(
        Q(file_id__in=visible_files(user).values('id'))
        | Q(folder_id__in=visible_folders(user).values('id'))
    )
    if within_days is not None:
        qs = qs.filter(created_at__gte=timezone.now() - timezone.timedelta(days=within_days))
    return qs
