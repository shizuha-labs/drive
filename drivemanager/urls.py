"""URL configuration for Shizuha Drive"""
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.utils import timezone
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
import os


def health_check(request):
    """Process liveness for Kubernetes.

    Recurring-mechanism health must not make Drive kill every serving replica.
    The fail-loud tier-scheduler signal lives at ``pending-tier-statusz``.
    """
    return JsonResponse({'status': 'healthy'})


def pending_tier_statusz(request):
    """Independent fail-loud invariant probe for the tier scheduler."""
    from drive.models import ScheduledMechanismRun, UserTier

    scheduler = {'state': 'never_run'}
    status_code = 200
    try:
        now = timezone.now()
        run, _ = ScheduledMechanismRun.objects.get_or_create(
            mechanism='apply_pending_tier_changes',
        )
        overdue_count = UserTier.objects.filter(
            pending_tier__isnull=False,
            next_billing_date__lte=now,
        ).count()
        if run and run.last_succeeded_at:
            age = now - run.last_succeeded_at
            stale_after = settings.DRIVE_TIER_SCHEDULER_STALE_HOURS * 3600
            stale = age.total_seconds() > stale_after
            scheduler = {
                'state': 'stale' if stale else 'healthy',
                'last_succeeded_at': run.last_succeeded_at.isoformat(),
                'last_failed_at': run.last_failed_at.isoformat() if run.last_failed_at else None,
                'consecutive_failures': run.consecutive_failures,
                'overdue_changes': overdue_count,
            }
            if stale:
                status_code = 503
        elif overdue_count:
            scheduler = {'state': 'overdue_without_success', 'overdue_changes': overdue_count}
            status_code = 503
        elif (now - run.created_at).total_seconds() > (
            settings.DRIVE_TIER_SCHEDULER_STALE_HOURS * 3600
        ):
            scheduler = {'state': 'never_succeeded_stale', 'overdue_changes': 0}
            status_code = 503
        else:
            scheduler['overdue_changes'] = 0
    except Exception as exc:
        scheduler = {'state': 'probe_error', 'error': str(exc)[:160]}
        status_code = 503
    return JsonResponse(
        {
            'status': 'healthy' if status_code == 200 else 'degraded',
            'pending_tier_scheduler': scheduler,
        },
        status=status_code,
    )


def offboarding_statusz(request):
    """Non-identifying liveness signal for the external fail-loud meta-probe."""
    from drive.models import DeletionProcessorState, DeletionRequest
    from django.utils import timezone

    state = DeletionProcessorState.objects.filter(
        pk=DeletionProcessorState.SINGLETON_PK,
    ).first()
    expired_pending = DeletionRequest.objects.filter(
        status=DeletionRequest.STATUS_PENDING,
        grace_expires_at__lte=timezone.now(),
    ).count()
    payload = {
        'mechanism': 'drive-offboarding-deletion',
        'last_started': state.last_started_at.isoformat() if state and state.last_started_at else None,
        'last_success': state.last_success_at.isoformat() if state and state.last_success_at else None,
        'last_failure': state.last_failure_at.isoformat() if state and state.last_failure_at else None,
        'last_probe': state.last_probe_at.isoformat() if state and state.last_probe_at else None,
        'consecutive_failures': state.consecutive_failures if state else 0,
        'expired_pending_count': expired_pending,
        'alert_delivery_status': state.last_alert_status if state else '',
    }
    if request.GET.get('export') == 'prometheus':
        def timestamp(value):
            return int(value.timestamp()) if value else 0

        metrics = {
            'drive_offboarding_processor_last_started_timestamp': timestamp(
                state.last_started_at if state else None
            ),
            'drive_offboarding_processor_last_success_timestamp': timestamp(
                state.last_success_at if state else None
            ),
            'drive_offboarding_processor_last_failure_timestamp': timestamp(
                state.last_failure_at if state else None
            ),
            'drive_offboarding_processor_last_probe_timestamp': timestamp(
                state.last_probe_at if state else None
            ),
            'drive_offboarding_processor_consecutive_failures': (
                state.consecutive_failures if state else 0
            ),
            'drive_offboarding_expired_pending_count': expired_pending,
            'drive_offboarding_alert_delivery_failed': int(bool(
                state and state.last_alert_status.startswith('failed:')
            )),
        }
        body = '\n'.join(f'{name} {value}' for name, value in metrics.items()) + '\n'
        return HttpResponse(body, content_type='text/plain; version=0.0.4; charset=utf-8')
    return JsonResponse(payload)

# Path to built Sphinx documentation
DOCS_ROOT = os.path.join(settings.BASE_DIR, 'docs_html')

urlpatterns = [
    # Health check endpoint (for container healthchecks)
    path('api/health/', health_check, name='health-check'),
    path(
        'api/pending-tier-statusz/',
        pending_tier_statusz,
        name='pending-tier-statusz',
    ),
    path('api/offboarding-statusz/', offboarding_statusz, name='offboarding-statusz'),

    # Prefixed URLs to match what nginx sends
    path('shizuha-drive/admin/', admin.site.urls),
    path('shizuha-drive/api/', include('drive.urls')),

    # API Documentation (prefixed)
    path('shizuha-drive/api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('shizuha-drive/api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('shizuha-drive/api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # Sphinx Documentation (prefixed)
    re_path(r'^shizuha-drive/docs/(?P<path>.*)$', serve, {'document_root': DOCS_ROOT}, name='docs'),

    # Also keep non-prefixed URLs for direct access
    path('admin/', admin.site.urls),
    path('api/', include('drive.urls')),

    # API Documentation (non-prefixed)
    path('api/schema/', SpectacularAPIView.as_view(), name='schema-direct'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema-direct'), name='swagger-ui-direct'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema-direct'), name='redoc-direct'),

    # Sphinx Documentation (non-prefixed)
    re_path(r'^docs/(?P<path>.*)$', serve, {'document_root': DOCS_ROOT}, name='docs-direct'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
