"""At-least-once delivery of the Drive occurrence outbox to the Metering Core.

PLAT-9234 / cortex#211 pattern: a remote meter outage can never silently
drop usage.  Delivery retries PENDING rows oldest-first with the SAME
(source, event_id) and a byte-identical envelope, so the core's replay
invariant classifies redelivery as replay success — never as a new fact and
never as a conflict.  Rows are never deleted; failures stay retryable with
the recorded error for alerting.

Secrets (drive-secrets, pre-landed per the CTX-980 mirror prerequisite):
``metering-core-url`` + ``metering-core-service-token``.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

import requests
from django.utils import timezone

from .metering import OccurrenceOutbox

logger = logging.getLogger(__name__)

DATASCHEMA = 'urn:shizuha:usage-event:1'
MAX_BATCH = 500


class EnvelopeError(Exception):
    """The outbox row cannot be rendered as a contract-valid envelope."""


class MeteringCoreError(Exception):
    def __init__(self, message, http_status=None, code=None):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


class MeteringCoreUnavailable(MeteringCoreError):
    pass


def _rfc3339(value) -> str:
    value = value.isoformat()
    if value.endswith('+00:00'):
        value = value[:-6] + 'Z'
    return value


def build_envelope(row) -> dict:
    """CloudEvents v1.0 envelope — DETERMINISTIC per row (replay-by-hash)."""
    measurements = (row.data or {}).get('measurements')
    if not isinstance(measurements, list) or not measurements:
        raise EnvelopeError('occurrence data carries no measurements')
    primary = measurements[0]
    if not isinstance(primary, dict):
        raise EnvelopeError('primary measurement is not an object')
    for field in ('meter', 'unit', 'quantity'):
        if not str(primary.get(field) or '').strip():
            raise EnvelopeError(f'primary measurement missing {field}')

    return {
        'specversion': '1.0',
        'type': row.event_type,
        'source': row.source,
        'id': row.event_id,
        'time': _rfc3339(row.occurred_at),
        'dataschema': DATASCHEMA,
        'subject': row.subject,
        'data': {
            'meter': primary['meter'],
            'unit': primary['unit'],
            'quantity': primary['quantity'],
            'billing_account_id': (row.data or {}).get('billing_account_id'),
            'actor_user_id': row.actor_user_id,
            'resource': (row.data or {}).get('resource'),
            'measurements': measurements,
            'request_id': (row.data or {}).get('request_id'),
        },
    }


def _core_config():
    """Read the metering core URL + service token.

    The drive-secrets keys ``metering-core-url`` + ``metering-core-service-token``
    (CTX-980 mirror prerequisite) are mounted into the container env by the
    deploy yaml; the env is the repo's config convention.
    """
    import os
    url = (os.environ.get('METERING_CORE_URL') or '').strip()
    token = (os.environ.get('METERING_CORE_SERVICE_TOKEN') or '').strip()
    return url, token


def ingest_events(batch: list[dict], post=None) -> Any:
    """POST one batch of envelopes to the core's usage ingest endpoint.

    The default (production) transport requires the metering core config and
    raises :class:`MeteringCoreUnavailable` when it is absent — an unconfigured
    core is "unavailable", never a crash. An injected ``post`` (test/dry-run
    transport) bypasses the secrets-config gate: the caller owns the transport,
    so the endpoint is only used as an opaque argument to it.
    """
    url, token = _core_config()
    if post is None:
        if not url or not token:
            raise MeteringCoreUnavailable('metering core not configured (secrets prerequisite)')
        post = requests.post
    endpoint = (url or '').rstrip('/') + '/usage/ingest'
    post = post or requests.post
    try:
        resp = post(
            endpoint,
            json=batch,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        raise MeteringCoreUnavailable(f'transport failure: {exc}') from exc
    if resp.status_code >= 500:
        raise MeteringCoreUnavailable(f'core 5xx: {resp.status_code}')
    if resp.status_code >= 400:
        try:
            code = resp.json().get('code')
        except Exception:
            code = None
        raise MeteringCoreError(
            f'core {resp.status_code}', http_status=resp.status_code, code=code
        )
    try:
        return resp.json()
    except ValueError:
        return {'status': 'accepted'}


def pending_occurrences(limit: int):
    return (
        OccurrenceOutbox.objects.filter(state=OccurrenceOutbox.State.PENDING)
        .order_by('recorded_at')[:limit]
    )


def mark_delivered(row):
    row.state = OccurrenceOutbox.State.DELIVERED
    row.delivered_at = timezone.now()
    row.last_error = ''
    row.save(update_fields=['state', 'delivered_at', 'last_error', 'updated_at']
             if hasattr(row, 'updated_at') else ['state', 'delivered_at', 'last_error'])


def mark_failed(row, error: str):
    row.state = OccurrenceOutbox.State.FAILED
    row.delivery_attempts += 1
    row.last_error = str(error)[:2000]
    row.save(update_fields=['state', 'delivery_attempts', 'last_error', 'updated_at']
             if hasattr(row, 'updated_at') else ['state', 'delivery_attempts', 'last_error'])


def deliver_pending(*, limit: int = 100, post=None) -> dict:
    """Deliver up to ``limit`` PENDING occurrences to the core.

    Returns a small stats dict: delivered / failed / conflict / unavailable.
    """
    stats = {'delivered': 0, 'failed': 0, 'conflict': 0, 'unavailable': 0}
    rows = list(pending_occurrences(limit=min(max(limit, 1), MAX_BATCH)))
    if not rows:
        return stats

    built = []
    for row in rows:
        try:
            built.append((row, build_envelope(row)))
        except EnvelopeError as exc:
            # A malformed occurrence can never become deliverable by retrying;
            # record it as failed so monitoring sees it (never dropped).
            mark_failed(row, f'envelope error: {exc}')
            stats['failed'] += 1

    if not built:
        return stats

    try:
        results = ingest_events([env for _r, env in built], post)
    except MeteringCoreUnavailable as exc:
        for row, _env in built:
            mark_failed(row, f'core unavailable: {exc}')
            stats['unavailable'] += 1
        logger.warning('metering delivery: core unavailable (%s); %d rows deferred', exc, len(built))
        return stats
    except MeteringCoreError as exc:
        if exc.http_status != 400:
            for row, _env in built:
                mark_failed(row, f'core error: {exc}')
                stats['failed'] += 1
            return stats
        # One malformed event fails the whole batch at the core.  Fall back to
        # per-event delivery so a single poison occurrence cannot starve the
        # rest of the queue (each row keeps its own retry state).
        results = []
        for row, env in built:
            try:
                per = ingest_events([env], post)
                results.append(per[0] if isinstance(per, list) and per else per)
            except MeteringCoreUnavailable as per_exc:
                results.append({'status': 'unavailable', 'detail': str(per_exc)})
            except MeteringCoreError as per_exc:
                results.append({'status': 'error', 'error': per_exc.code, 'detail': str(per_exc)})

    if isinstance(results, dict):
        results = results.get('results', [results])

    for (row, _env), result in zip(built, list(results or []) + [None] * (len(built) - len(results or []))):
        outcome = str((result or {}).get('status') or (result or {}).get('outcome') or '')
        if outcome in ('accepted', 'replayed', 'replay', 'duplicate', 'ok', 'delivered'):
            mark_delivered(row)
            stats['delivered'] += 1
        elif outcome in ('conflict', 'idempotency_conflict'):
            # HLD §3 invariant 2: never replace the original; alert and stop
            # retrying this identity (it would conflict forever).
            mark_failed(row, f'idempotency conflict at core: {result}')
            stats['conflict'] += 1
        elif outcome == 'unavailable':
            mark_failed(row, f'core unavailable: {(result or {}).get("detail", "")}')
            stats['unavailable'] += 1
        else:
            mark_failed(row, f'core rejected: {result}')
            stats['failed'] += 1
    return stats


def outbox_age_seconds() -> float | None:
    """Age of the oldest PENDING occurrence — the HLD §5 outbox-age monitor."""
    oldest = (
        OccurrenceOutbox.objects.filter(state=OccurrenceOutbox.State.PENDING)
        .order_by('recorded_at')
        .values_list('recorded_at', flat=True)
        .first()
    )
    if oldest is None:
        return None
    return (timezone.now() - oldest).total_seconds()
