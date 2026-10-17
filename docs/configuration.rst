Configuration
=============

Shizuha Drive can be configured through environment variables and Django settings.

Storage Configuration
---------------------

Local Storage
^^^^^^^^^^^^^

By default, Shizuha Drive uses Django's local file storage:

.. code-block:: python

   # Default behavior when USE_S3_STORAGE is not set
   STORAGES = {
       "default": {
           "BACKEND": "django.core.files.storage.FileSystemStorage",
       },
   }

Files are stored in ``MEDIA_ROOT`` (default: ``/app/media/``).

S3/MinIO Storage
^^^^^^^^^^^^^^^^

For production, enable S3-compatible storage:

.. code-block:: bash

   USE_S3_STORAGE=1
   AWS_ACCESS_KEY_ID=your-access-key
   AWS_SECRET_ACCESS_KEY=your-secret-key
   AWS_STORAGE_BUCKET_NAME=shizuha-drive
   AWS_S3_ENDPOINT_URL=http://minio:9000

This uses django-storages with the S3Boto3Storage backend.

Quota Configuration
-------------------

Default Quotas
^^^^^^^^^^^^^^

Set default storage quotas for new users:

.. code-block:: python

   # Default quota in bytes (10 GB)
   DEFAULT_STORAGE_QUOTA = 10 * 1024 * 1024 * 1024

Per-User Quotas
^^^^^^^^^^^^^^^

Override quotas for specific users via the API or management command:

.. code-block:: bash

   # Set 50GB quota for user
   python manage.py set_quota user@example.com --size 50GB

Upload Limits
-------------

Configure maximum file sizes:

.. code-block:: python

   # Maximum upload size (100 MB)
   DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024
   FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024

For nginx/ingress, also configure:

.. code-block:: nginx

   client_max_body_size 100M;

Celery Configuration
--------------------

Async task processing configuration:

.. code-block:: python

   CELERY_BROKER_URL = os.environ.get(
       'CELERY_BROKER_URL',
       'redis://localhost:6379/8'
   )
   CELERY_RESULT_BACKEND = CELERY_BROKER_URL

   # Task settings
   CELERY_TASK_SERIALIZER = 'json'
   CELERY_RESULT_SERIALIZER = 'json'
   CELERY_ACCEPT_CONTENT = ['json']
   CELERY_TIMEZONE = 'UTC'

Scheduled Tasks
^^^^^^^^^^^^^^^

Configure scheduled task timing:

.. code-block:: python

   from celery.schedules import crontab

   CELERY_BEAT_SCHEDULE = {
       'cleanup-trash': {
           'task': 'drive.tasks.cleanup_trash',
           'schedule': crontab(hour=2, minute=0),
       },
       'cleanup-orphaned-files': {
           'task': 'drive.tasks.cleanup_orphaned_files',
           'schedule': crontab(hour=3, minute=0),
       },
       'recalculate-quotas': {
           'task': 'drive.tasks.recalculate_quotas',
           'schedule': crontab(hour=4, minute=0),
       },
   }

Authentication Configuration
----------------------------

Shizuha Drive uses JWT tokens from Shizuha ID:

.. code-block:: python

   # JWT configuration
   JWT_SECRET = os.environ.get('JWT_SECRET')
   JWT_ALGORITHM = 'HS256'

   # Token settings
   JWT_ACCESS_TOKEN_EXPIRY = 3600  # 1 hour
   JWT_REFRESH_TOKEN_EXPIRY = 604800  # 7 days

CORS Configuration
------------------

For frontend development:

.. code-block:: python

   CORS_ALLOWED_ORIGINS = [
       "http://localhost:5179",
       "http://localhost:30080",
   ]
   CORS_ALLOW_CREDENTIALS = True

Thumbnail Configuration
-----------------------

Configure thumbnail generation:

.. code-block:: python

   THUMBNAIL_SIZES = {
       'small': (128, 128),
       'medium': (256, 256),
       'large': (512, 512),
   }

   # Supported image formats
   THUMBNAIL_FORMATS = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']

Logging Configuration
---------------------

Configure logging levels:

.. code-block:: python

   LOGGING = {
       'version': 1,
       'disable_existing_loggers': False,
       'handlers': {
           'console': {
               'class': 'logging.StreamHandler',
           },
       },
       'root': {
           'handlers': ['console'],
           'level': os.environ.get('LOG_LEVEL', 'INFO'),
       },
       'loggers': {
           'drive': {
               'level': 'DEBUG',
               'handlers': ['console'],
               'propagate': False,
           },
       },
   }

Security Settings
-----------------

Production security settings:

.. code-block:: python

   # In production
   DEBUG = False
   ALLOWED_HOSTS = ['drive.yourdomain.com']
   SECURE_SSL_REDIRECT = True
   SESSION_COOKIE_SECURE = True
   CSRF_COOKIE_SECURE = True

Helm Values Reference
---------------------

For Kubernetes deployments, configure via Helm values:

.. code-block:: yaml

   shizuhaDrive:
     enabled: true
     replicaCount: 1
     image:
       repository: docker.io/library/shizuha-drive
       tag: latest
     resources:
       requests:
         cpu: 200m
         memory: 512Mi
       limits:
         cpu: 1000m
         memory: 2Gi
     env:
       DEBUG: "0"
       LOG_LEVEL: "INFO"

   minio:
     enabled: true
     nodeHostname: "storage-node"
     dataPath: "/data/minio"
     rootUser: "minioadmin"
     rootPassword: "your-secure-password"
     bucketName: "shizuha-drive"

See :doc:`admin/deployment` for complete Helm configuration reference.
