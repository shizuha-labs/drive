"""
URL configuration for Shizuha Drive API
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    FolderViewSet, FileViewSet, ShareViewSet,
    PublicShareView, StorageQuotaView, TrashViewSet,
    ActivityViewSet, DashboardView,
    InternalStorageReportView, InternalCanUploadView, ResumableUploadViewSet,
    DriveTelemetryView,  # DRV-23
    WaitlistView,  # DRV-22
    StorageTierViewSet, UserTierView,
    ExportView, OffboardingView,  # VEN-153
    ChangeFeedView, ChangeBootstrapView, ConflictArtifactView,  # DRV-105
)

router = DefaultRouter()
router.register(r'folders', FolderViewSet, basename='folder')
router.register(r'files', FileViewSet, basename='file')
router.register(r'uploads/resumable', ResumableUploadViewSet, basename='resumable-upload')
router.register(r'shares', ShareViewSet, basename='share')
router.register(r'trash', TrashViewSet, basename='trash')
router.register(r'activity', ActivityViewSet, basename='activity')

resumable_uploads = ResumableUploadViewSet.as_view({
    'post': 'create',
})
resumable_upload_detail = ResumableUploadViewSet.as_view({
    'get': 'retrieve',
    'delete': 'destroy',
})
resumable_upload_chunks = ResumableUploadViewSet.as_view({
    'put': 'chunks',
})
resumable_upload_commit = ResumableUploadViewSet.as_view({
    'post': 'commit',
})

urlpatterns = [
    # DRV-33 HLD contract aliases. The legacy DRV-10
    # /uploads/resumable/ router remains available for existing clients.
    path('uploads', resumable_uploads, name='upload-session-list-noslash'),
    path('uploads/', resumable_uploads, name='upload-session-list'),
    path('uploads/<uuid:pk>', resumable_upload_detail, name='upload-session-detail-noslash'),
    path('uploads/<uuid:pk>/', resumable_upload_detail, name='upload-session-detail'),
    path('uploads/<uuid:pk>/chunks', resumable_upload_chunks, name='upload-session-chunks-noslash'),
    path('uploads/<uuid:pk>/chunks/', resumable_upload_chunks, name='upload-session-chunks'),
    path('uploads/<uuid:pk>/commit', resumable_upload_commit, name='upload-session-commit-noslash'),
    path('uploads/<uuid:pk>/commit/', resumable_upload_commit, name='upload-session-commit'),

    # Router URLs
    path('', include(router.urls)),

    # Dashboard
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # Storage quota
    path('quota/', StorageQuotaView.as_view(), name='quota'),

    # Public share access (no auth required)
    path('public/<str:token>/', PublicShareView.as_view(), name='public-share'),

    # DRV-22: free-validation waitlist (POST public submit, GET staff inspect/export)
    path('waitlist/', WaitlistView.as_view(), name='waitlist'),

    # Internal API for cross-service storage quota management
    path('internal/storage/report/', InternalStorageReportView.as_view(), name='internal-storage-report'),
    path('internal/storage/can-upload/', InternalCanUploadView.as_view(), name='internal-can-upload'),

    # DRV-23: free-tier usage telemetry (staff; ?export=prometheus)
    path('internal/telemetry/', DriveTelemetryView.as_view(), name='internal-telemetry'),

    # Storage tiers — my/ must come before <slug:slug>/
    path('tiers/my/', UserTierView.as_view(), name='my-tier'),
    path('tiers/', StorageTierViewSet.as_view({'get': 'list'}), name='tier-list'),
    path('tiers/<slug:slug>/', StorageTierViewSet.as_view({'get': 'retrieve'}), name='tier-detail'),
    # VEN-153: data export and offboarding
    path('export/', ExportView.as_view(), name='export'),
    path('offboard/', OffboardingView.as_view(), name='offboard'),

    # DRV-105: change-feed / delta contract
    path('changes/', ChangeFeedView.as_view(), name='change-feed'),
    path('changes/bootstrap/', ChangeBootstrapView.as_view(), name='change-bootstrap'),
    path('changes/conflicts/', ConflictArtifactView.as_view(), name='change-conflict'),
]
