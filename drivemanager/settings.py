"""
Django settings for Shizuha Drive - File Storage Service
"""

import os
from pathlib import Path
from datetime import timedelta

from celery.schedules import crontab

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

def _required_credential(name, fallback=None):
    """Keep configured bytes; no shared or ambient credential identity."""
    value = os.environ.get(name, fallback)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{name} must be configured")
    return value


SECRET_KEY = _required_credential("SECRET_KEY")
# Preserve the existing explicit installation-key fallback for legacy JWTs.
JWT_SECRET_KEY = _required_credential("JWT_SECRET_KEY", SECRET_KEY)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', '0') == '1'

ALLOWED_HOSTS = ['*']

# DRV-90: the ingress terminates TLS and strips the /drive prefix
# (rewrite-target: /$1), so DRF absolute URLs (pagination next/previous)
# would otherwise degrade to http:// and lose the prefix. Trust the
# ingress-provided X-Forwarded-Proto for scheme generation.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'django_filters',
    'drf_spectacular',
    # Local apps
    'drive',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'drive.middleware.ServiceAccessMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'drivemanager.middleware.ScriptNameMiddleware',
]

ROOT_URLCONF = 'drivemanager.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'drivemanager.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'shizuha_drive'),
        'USER': os.environ.get('POSTGRES_USER', 'postgres'),
        'PASSWORD': _required_credential('POSTGRES_PASSWORD'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# =============================================================================
# Storage Configuration
# =============================================================================
# Supports both local filesystem and S3-compatible storage (MinIO)
USE_S3_STORAGE = os.environ.get('USE_S3_STORAGE', '0') == '1'

if USE_S3_STORAGE:
    # S3-compatible storage backend (MinIO)
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

    # AWS S3 / MinIO settings
    AWS_ACCESS_KEY_ID = _required_credential('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = _required_credential('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'shizuha-drive')
    AWS_S3_ENDPOINT_URL = os.environ.get('AWS_S3_ENDPOINT_URL', 'http://localhost:9000')
    AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')

    # MinIO-specific settings
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_ADDRESSING_STYLE = 'path'  # Required for MinIO

    # URL settings for media files
    AWS_S3_CUSTOM_DOMAIN = None  # Use default S3 endpoint
    AWS_QUERYSTRING_AUTH = True  # Use presigned URLs for private files
    AWS_QUERYSTRING_EXPIRE = 3600  # URL expiry in seconds (1 hour)

    # Media URL for S3
    MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'
    MEDIA_ROOT = ''  # Not used with S3
else:
    # Local filesystem storage (default)
    MEDIA_URL = 'shizuha-drive/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'drive.authentication.ShizuhaJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
        'drive.permissions.ServiceAccessPermission',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# JWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'SIGNING_KEY': JWT_SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# CORS
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = [
    'http://localhost:5182',
    'http://localhost:30080',
    'https://shizuha.com',
    'https://erp.shizuha.com',
]

# Celery
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/8')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', CELERY_BROKER_URL)
CELERY_TASK_DEFAULT_QUEUE = 'shizuha_drive'
CELERY_TASK_DEFAULT_EXCHANGE = 'shizuha_drive'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes

# VEN-153 fail-loud owner and liveness contract. The service token is optional
# at import time so local checks remain hermetic; production must inject it for
# Connect delivery, while the durable processor state records any delivery gap.
DRIVE_OFFBOARDING_ALERT_OWNER_USERNAME = os.environ.get(
    'DRIVE_OFFBOARDING_ALERT_OWNER_USERNAME', 'ryo'
)
DRIVE_OFFBOARDING_CONNECT_API_URL = os.environ.get(
    'DRIVE_OFFBOARDING_CONNECT_API_URL',
    'http://shizuha-connect.shizuha.svc.cluster.local:8004/api',
)
DRIVE_OFFBOARDING_CONNECT_TOKEN = os.environ.get(
    'DRIVE_OFFBOARDING_CONNECT_TOKEN', os.environ.get('SHIZUHA_AGENT_TOKEN', '')
)
DRIVE_OFFBOARDING_STALE_HOURS = int(os.environ.get('DRIVE_OFFBOARDING_STALE_HOURS', '36'))

# DRV-7: trash retention + auto-purge. Trashed items older than N days are
# permanently purged by the scheduled job; restore works within the window.
DRIVE_TRASH_RETENTION_DAYS = int(os.environ.get('DRIVE_TRASH_RETENTION_DAYS', '30'))
DRIVE_TRASH_RESTORE_WINDOW_DAYS = (
    int(os.environ['DRIVE_TRASH_RESTORE_WINDOW_DAYS'])
    if os.environ.get('DRIVE_TRASH_RESTORE_WINDOW_DAYS')
    else None
)

# DRV-41: keep the newest N historical FileVersion rows per file. The current
# live File.version is never pruned; this bound applies only to historical rows.
DRIVE_VERSION_RETENTION_COUNT = int(os.environ.get('DRIVE_VERSION_RETENTION_COUNT', '50'))
CELERY_BEAT_SCHEDULE = {
    'purge-expired-trash': {
        'task': 'drive.tasks.purge_expired_trash',
        'schedule': timedelta(days=1),  # daily
    },
    'prune-file-versions': {
        'task': 'drive.tasks.prune_file_versions',
        'schedule': timedelta(days=1),  # daily
    },
    'apply-pending-storage-tier-changes': {
        'task': 'drive.tasks.apply_pending_tier_changes',
        # A relative daily countdown is reset by every pod rollout. Several
        # ordinary deploys therefore starved this task until its health record
        # became stale. Wall-clock quarter-hour dispatch is restart-independent.
        'schedule': crontab(minute='*/15'),
    },
    'process-expired-deletion-requests': {
        'task': 'drive.tasks.process_expired_deletion_requests',
        # A relative daily countdown is reset by every pod rollout. Several
        # ordinary deploys therefore starved this task until its health
        # record (DeletionProcessorState.last_success_at, consumed by the
        # drive-offboarding-deletion mechanism-liveness probe) became stale.
        # Wall-clock daily dispatch is restart-independent.
        'schedule': crontab(hour=3, minute=7),
    },
    'probe-expired-deletion-requests': {
        'task': 'drive.tasks.probe_expired_deletion_requests',
        # Same restart-independence rationale as the daily processor: a
        # relative hourly countdown resets on every pod rollout and can
        # starve the fail-closed overdue-request probe for hours.
        'schedule': crontab(minute=37),
    },
    'recalculate-quotas': {
        'task': 'drive.tasks.recalculate_quotas',
        # DRV-112: the stored quota.used_bytes can drift from the live File
        # rows (incremental add/remove vs. direct writes). A wall-clock daily
        # reconciliation keeps the stored quota authoritative for enforcement
        # (reserve_upload) and any consumer that reads the stored field.
        'schedule': crontab(hour=4, minute=13),
    },
}

# API Documentation
SPECTACULAR_SETTINGS = {
    'TITLE': 'Shizuha Drive API',
    'DESCRIPTION': 'File storage and sharing service',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# Shizuha ID Integration
SHIZUHA_ID_API_URL = os.environ.get('SHIZUHA_ID_API_URL', 'http://shizuha-id:8001/api')
SHIZUHA_ADMIN_API_URL = os.environ.get('SHIZUHA_ADMIN_API_URL', 'http://shizuha-admin:8003/api')
DRIVE_TIER_ALERT_RECIPIENT_EMAIL = os.environ.get(
    'DRIVE_TIER_ALERT_RECIPIENT_EMAIL',
    'kai@shizuha.com',
)
DRIVE_TIER_SCHEDULER_STALE_HOURS = int(
    os.environ.get('DRIVE_TIER_SCHEDULER_STALE_HOURS', '36')
)

# File Upload Settings
MAX_UPLOAD_SIZE = int(os.environ.get('MAX_UPLOAD_SIZE', 104857600))  # 100MB default
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE

# DRV-6: emit a non-blocking quota warning once usage reaches this percent.
DRIVE_QUOTA_WARN_THRESHOLD_PERCENT = int(os.environ.get('DRIVE_QUOTA_WARN_THRESHOLD_PERCENT', '90'))

# Allowed file types (MIME types)
ALLOWED_FILE_TYPES = [
    # Documents
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain',
    'text/csv',
    'text/markdown',
    # Images
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp',
    'image/svg+xml',
    # Archives
    'application/zip',
    'application/x-tar',
    'application/gzip',
    'application/x-7z-compressed',
    'application/x-rar-compressed',
    # Media
    'audio/mpeg',
    'audio/wav',
    'audio/ogg',
    'video/mp4',
    'video/webm',
    'video/quicktime',
    # Code
    'application/json',
    'application/xml',
    'text/html',
    'text/css',
    'text/javascript',
    'application/javascript',
]

# Thumbnail settings
THUMBNAIL_SIZES = {
    'small': (128, 128),
    'medium': (256, 256),
    'large': (512, 512),
}

# =============================================================================
# Email Configuration (VEN-150)
# =============================================================================
# Default: console backend for dev. Set EMAIL_HOST/EMAIL_PORT/EMAIL_HOST_USER/
# EMAIL_HOST_PASSWORD in production to use SMTP (e.g. SendGrid, Mailgun, Resend).
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
DEFAULT_FROM_EMAIL = os.environ.get(
    'DEFAULT_FROM_EMAIL',
    'Shizuha Drive <noreply@shizuha.com>',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', '1') == '1'
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', '0') == '1'

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': '/var/log/django/drive.log',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'drive': {
            'handlers': ['console', 'file'] if not DEBUG else ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}


# ─────────── OAuth 2.0 Phase 2/4 — advisory claims + enforcement flag ───────
# Used by ShizuhaJWTAuthentication to log aud/iss mismatches and optionally
# reject tokens whose `aud` doesn't include this service. Defaults keep
# Phase 2 (advisory only) behavior; flip SHIZUHA_AUD_ENFORCE to True once
# advisory logs show clean traffic.
import os as _os
SHIZUHA_SERVICE_NAME = _os.environ.get('SHIZUHA_SERVICE_NAME', 'drive')
SHIZUHA_EXPECTED_ISSUER = _os.environ.get(
    'SHIZUHA_EXPECTED_ISSUER',
    'http://s1.tail.shizuha.com/id',
)
SHIZUHA_AUD_ENFORCE = _os.environ.get('SHIZUHA_AUD_ENFORCE', '').lower() in ('1', 'true', 'yes')
