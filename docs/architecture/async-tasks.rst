Async Tasks
===========

Shizuha Drive uses Celery for asynchronous task processing, ensuring responsive API responses and reliable background operations.

Task Architecture
-----------------

.. code-block:: text

   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
   │  Django API     │     │     Redis       │     │  Celery Worker  │
   │                 │     │   (Broker)      │     │                 │
   │  1. Upload file │────▶│                 │────▶│ 2. Process file │
   │  3. Return ID   │     │  Task Queue     │     │    - Metadata   │
   │                 │     │                 │     │    - Thumbnails │
   └─────────────────┘     └─────────────────┘     └─────────────────┘
                                   ▲
                                   │
                           ┌───────┴───────┐
                           │  Celery Beat  │
                           │  (Scheduler)  │
                           │               │
                           │ - Cleanup     │
                           │ - Quotas      │
                           └───────────────┘

Configuration
-------------

**Celery Settings:**

.. code-block:: python

   # settings.py
   CELERY_BROKER_URL = os.environ.get(
       'CELERY_BROKER_URL',
       'redis://localhost:6379/8'
   )
   CELERY_RESULT_BACKEND = CELERY_BROKER_URL
   CELERY_TASK_SERIALIZER = 'json'
   CELERY_RESULT_SERIALIZER = 'json'
   CELERY_ACCEPT_CONTENT = ['json']
   CELERY_TIMEZONE = 'UTC'

**Celery App:**

.. code-block:: python

   # drivemanager/celery.py
   from celery import Celery

   app = Celery('drivemanager')
   app.config_from_object('django.conf:settings', namespace='CELERY')
   app.autodiscover_tasks()

Task Definitions
----------------

Process File Upload
^^^^^^^^^^^^^^^^^^^

Triggered after a file is uploaded to extract metadata and generate thumbnails.

.. code-block:: python

   @shared_task(bind=True, max_retries=3)
   def process_file_upload(self, file_id):
       """
       Process an uploaded file:
       1. Extract metadata (MIME type, dimensions, duration)
       2. Calculate checksums (MD5, SHA-256)
       3. Generate thumbnails (for images/videos)
       4. Update quota usage
       """
       try:
           file_obj = File.objects.get(id=file_id)

           # Extract metadata
           extract_file_metadata(file_obj)

           # Calculate checksums
           calculate_checksums(file_obj)

           # Generate thumbnails for images/videos
           if file_obj.file_type in ['image', 'video']:
               generate_thumbnails.delay(file_id)

           # Update quota
           update_quota_usage(file_obj.owner_id)

       except File.DoesNotExist:
           logger.warning(f"File {file_id} not found for processing")
       except Exception as exc:
           raise self.retry(exc=exc, countdown=60)

**Task Flow:**

1. File uploaded via API → saved to MinIO
2. Database record created with basic info
3. ``process_file_upload.delay(file_id)`` called
4. Worker picks up task from queue
5. Metadata extracted, checksums calculated
6. Thumbnails generated (if applicable)
7. Quota updated

Generate Thumbnails
^^^^^^^^^^^^^^^^^^^

Creates multiple thumbnail sizes for image and video files.

.. code-block:: python

   @shared_task(bind=True, max_retries=2)
   def generate_thumbnails(self, file_id):
       """
       Generate thumbnails in multiple sizes:
       - small: 128x128
       - medium: 256x256
       - large: 512x512
       """
       file_obj = File.objects.get(id=file_id)

       sizes = [
           ('small', 128, 128),
           ('medium', 256, 256),
           ('large', 512, 512),
       ]

       for size_name, width, height in sizes:
           try:
               thumbnail = create_thumbnail(file_obj, width, height)
               Thumbnail.objects.update_or_create(
                   file=file_obj,
                   size_type=size_name,
                   defaults={
                       'image': thumbnail,
                       'width': width,
                       'height': height
                   }
               )
           except Exception as e:
               logger.error(f"Thumbnail generation failed: {e}")

Scheduled Tasks
---------------

Celery Beat schedules these tasks to run periodically.

**Beat Schedule:**

.. code-block:: python

   # settings.py
   CELERY_BEAT_SCHEDULE = {
       'cleanup-trash': {
           'task': 'drive.tasks.cleanup_trash',
           'schedule': crontab(hour=2, minute=0),  # 2:00 AM daily
       },
       'cleanup-orphaned-files': {
           'task': 'drive.tasks.cleanup_orphaned_files',
           'schedule': crontab(hour=3, minute=0),  # 3:00 AM daily
       },
       'recalculate-quotas': {
           'task': 'drive.tasks.recalculate_quotas',
           'schedule': crontab(hour=4, minute=0),  # 4:00 AM daily
       },
   }

Cleanup Trash
^^^^^^^^^^^^^

Permanently deletes files that have been in trash for more than 30 days.

.. code-block:: python

   @shared_task
   def cleanup_trash():
       """
       Delete files/folders that have been in trash > 30 days
       """
       threshold = timezone.now() - timedelta(days=30)

       # Delete old trashed files
       old_files = File.objects.filter(
           is_trashed=True,
           trashed_at__lt=threshold
       )

       for file_obj in old_files:
           # Delete from storage
           if file_obj.file:
               default_storage.delete(file_obj.file.name)
           # Delete thumbnails
           file_obj.thumbnails.all().delete()
           # Delete versions
           file_obj.versions.all().delete()
           # Delete record
           file_obj.delete()

       # Delete old trashed folders (cascade deletes contents)
       Folder.objects.filter(
           is_trashed=True,
           trashed_at__lt=threshold
       ).delete()

       logger.info(f"Trash cleanup completed: {old_files.count()} files deleted")

Cleanup Orphaned Files
^^^^^^^^^^^^^^^^^^^^^^

Removes files from storage that don't have corresponding database records.

.. code-block:: python

   @shared_task
   def cleanup_orphaned_files():
       """
       Remove files from storage without database records
       """
       from django.core.files.storage import default_storage

       # Get all file paths from database
       db_files = set(
           File.objects.values_list('file', flat=True)
       )

       # List files in storage
       # Note: This implementation needs to be storage-backend aware
       orphaned = []

       # For S3/MinIO, use boto3 to list objects
       # For local storage, use os.walk

       for orphan_path in orphaned:
           default_storage.delete(orphan_path)
           logger.info(f"Deleted orphaned file: {orphan_path}")

Recalculate Quotas
^^^^^^^^^^^^^^^^^^

Recalculates storage usage for all users to ensure accuracy.

.. code-block:: python

   @shared_task
   def recalculate_quotas():
       """
       Recalculate storage usage for all users
       """
       from django.db.models import Sum, Count

       # Get all unique owner IDs
       owner_ids = File.objects.values_list(
           'owner_id', flat=True
       ).distinct()

       for owner_id in owner_ids:
           # Calculate actual usage
           usage = File.objects.filter(
               owner_id=owner_id,
               is_trashed=False
           ).aggregate(
               total_size=Sum('size'),
               file_count=Count('id')
           )

           # Update quota record
           StorageQuota.objects.update_or_create(
               owner_id=owner_id,
               defaults={
                   'used_bytes': usage['total_size'] or 0,
                   'file_count': usage['file_count'] or 0
               }
           )

       logger.info(f"Quota recalculation completed for {len(owner_ids)} users")

Task Monitoring
---------------

**View Task Queue:**

.. code-block:: bash

   # Using Flower (Celery monitoring)
   celery -A drivemanager flower

   # Or via Redis CLI
   redis-cli -n 8 LLEN celery

**Check Worker Status:**

.. code-block:: bash

   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     celery -A drivemanager inspect active

**View Recent Tasks:**

.. code-block:: bash

   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     celery -A drivemanager inspect reserved

Error Handling
--------------

Tasks use automatic retry with exponential backoff:

.. code-block:: python

   @shared_task(bind=True, max_retries=3, default_retry_delay=60)
   def process_file_upload(self, file_id):
       try:
           # ... process file ...
       except TransientError as exc:
           # Retry with exponential backoff
           raise self.retry(exc=exc, countdown=2 ** self.request.retries * 60)
       except PermanentError as exc:
           # Don't retry, log and fail
           logger.error(f"Permanent failure processing {file_id}: {exc}")
           raise

Task Priorities
---------------

Tasks can be routed to different queues based on priority:

.. code-block:: python

   # High priority (user-facing)
   process_file_upload.apply_async(
       args=[file_id],
       queue='high_priority'
   )

   # Low priority (background)
   cleanup_trash.apply_async(queue='low_priority')

**Worker Configuration:**

.. code-block:: bash

   # High priority worker
   celery -A drivemanager worker -Q high_priority -c 4

   # Low priority worker
   celery -A drivemanager worker -Q low_priority -c 2
