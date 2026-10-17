Data Model
==========

Shizuha Drive's data model is designed around a hierarchical file system with sharing and versioning capabilities.

Entity Relationship Diagram
---------------------------

.. code-block:: text

   ┌─────────────────────┐       ┌─────────────────────┐
   │       Folder        │       │        File         │
   ├─────────────────────┤       ├─────────────────────┤
   │ id (UUID, PK)       │       │ id (UUID, PK)       │
   │ name                │◄──────│ folder (FK)         │
   │ parent (FK, self)   │       │ owner_id            │
   │ owner_id            │       │ name                │
   │ is_starred          │       │ file (FileField)    │
   │ is_trashed          │       │ size                │
   │ trashed_at          │       │ mime_type           │
   │ created_at          │       │ file_type           │
   │ updated_at          │       │ md5_checksum        │
   └─────────────────────┘       │ sha256_checksum     │
          ▲                      │ is_starred          │
          │                      │ is_trashed          │
          │                      │ trashed_at          │
          │                      │ width, height       │
          │                      │ duration            │
          │                      │ created_at          │
          │                      │ updated_at          │
          │                      └─────────────────────┘
          │                               │
          │                               ▼
          │                      ┌─────────────────────┐
          │                      │    FileVersion      │
          │                      ├─────────────────────┤
          │                      │ id (UUID, PK)       │
          │                      │ file (FK)           │
          │                      │ version_number      │
          │                      │ file_data           │
          │                      │ size                │
          │                      │ created_by          │
          │                      │ created_at          │
          │                      └─────────────────────┘
          │
   ┌──────┴──────────────┐       ┌─────────────────────┐
   │       Share         │       │   StorageQuota      │
   ├─────────────────────┤       ├─────────────────────┤
   │ id (UUID, PK)       │       │ id (PK)             │
   │ file (FK, nullable) │       │ owner_id (unique)   │
   │ folder (FK,nullable)│       │ quota_bytes         │
   │ shared_by           │       │ used_bytes          │
   │ shared_with         │       │ file_count          │
   │ permission          │       │ updated_at          │
   │ is_public           │       └─────────────────────┘
   │ public_token        │
   │ password_hash       │       ┌─────────────────────┐
   │ expires_at          │       │     Thumbnail       │
   │ access_count        │       ├─────────────────────┤
   │ created_at          │       │ id (UUID, PK)       │
   └─────────────────────┘       │ file (FK)           │
                                 │ size_type           │
                                 │ image               │
                                 │ width, height       │
                                 │ created_at          │
                                 └─────────────────────┘

Core Models
-----------

File Model
^^^^^^^^^^

.. code-block:: python

   class File(models.Model):
       id = models.UUIDField(primary_key=True, default=uuid.uuid4)
       folder = models.ForeignKey('Folder', null=True, blank=True,
                                  on_delete=models.CASCADE)
       owner_id = models.CharField(max_length=50, db_index=True)
       name = models.CharField(max_length=255)
       file = models.FileField(upload_to=file_upload_path)
       size = models.BigIntegerField(default=0)
       mime_type = models.CharField(max_length=255, blank=True)
       file_type = models.CharField(max_length=20, choices=FILE_TYPE_CHOICES)
       md5_checksum = models.CharField(max_length=32, blank=True)
       sha256_checksum = models.CharField(max_length=64, blank=True)
       is_starred = models.BooleanField(default=False)
       is_trashed = models.BooleanField(default=False)
       trashed_at = models.DateTimeField(null=True, blank=True)
       width = models.IntegerField(null=True, blank=True)  # Images/videos
       height = models.IntegerField(null=True, blank=True)
       duration = models.FloatField(null=True, blank=True)  # Audio/video
       created_at = models.DateTimeField(auto_now_add=True)
       updated_at = models.DateTimeField(auto_now=True)

**File Types:**

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Type
     - MIME Types
   * - ``document``
     - PDF, Word, Excel, PowerPoint, text files
   * - ``image``
     - JPEG, PNG, GIF, WebP, SVG
   * - ``video``
     - MP4, WebM, AVI, MOV
   * - ``audio``
     - MP3, WAV, OGG, FLAC
   * - ``archive``
     - ZIP, TAR, RAR, 7z
   * - ``other``
     - Everything else

Folder Model
^^^^^^^^^^^^

.. code-block:: python

   class Folder(models.Model):
       id = models.UUIDField(primary_key=True, default=uuid.uuid4)
       name = models.CharField(max_length=255)
       parent = models.ForeignKey('self', null=True, blank=True,
                                  on_delete=models.CASCADE,
                                  related_name='children')
       owner_id = models.CharField(max_length=50, db_index=True)
       is_starred = models.BooleanField(default=False)
       is_trashed = models.BooleanField(default=False)
       trashed_at = models.DateTimeField(null=True, blank=True)
       created_at = models.DateTimeField(auto_now_add=True)
       updated_at = models.DateTimeField(auto_now=True)

       class Meta:
           unique_together = ['name', 'parent', 'owner_id']

**Folder Path Resolution:**

.. code-block:: python

   def get_path(self):
       """Get full path from root"""
       ancestors = []
       current = self
       while current:
           ancestors.insert(0, current.name)
           current = current.parent
       return '/' + '/'.join(ancestors)

Share Model
^^^^^^^^^^^

.. code-block:: python

   class Share(models.Model):
       PERMISSION_CHOICES = [
           ('VIEW', 'View only'),
           ('EDIT', 'Can edit'),
       ]

       id = models.UUIDField(primary_key=True, default=uuid.uuid4)
       file = models.ForeignKey(File, null=True, on_delete=models.CASCADE)
       folder = models.ForeignKey(Folder, null=True, on_delete=models.CASCADE)
       shared_by = models.CharField(max_length=50)  # Owner user ID
       shared_with = models.CharField(max_length=50, null=True)  # Target user
       permission = models.CharField(max_length=10, choices=PERMISSION_CHOICES)
       is_public = models.BooleanField(default=False)
       public_token = models.CharField(max_length=32, unique=True, null=True)
       password_hash = models.CharField(max_length=128, blank=True)
       expires_at = models.DateTimeField(null=True, blank=True)
       access_count = models.IntegerField(default=0)
       created_at = models.DateTimeField(auto_now_add=True)

StorageQuota Model
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   class StorageQuota(models.Model):
       owner_id = models.CharField(max_length=50, unique=True)
       quota_bytes = models.BigIntegerField(default=5368709120)  # 5GB
       used_bytes = models.BigIntegerField(default=0)
       file_count = models.IntegerField(default=0)
       updated_at = models.DateTimeField(auto_now=True)

       @property
       def remaining_bytes(self):
           return max(0, self.quota_bytes - self.used_bytes)

       def can_upload(self, file_size):
           return self.remaining_bytes >= file_size

FileVersion Model
^^^^^^^^^^^^^^^^^

.. code-block:: python

   class FileVersion(models.Model):
       id = models.UUIDField(primary_key=True, default=uuid.uuid4)
       file = models.ForeignKey(File, on_delete=models.CASCADE,
                               related_name='versions')
       version_number = models.IntegerField()
       file_data = models.FileField(upload_to='versions/')
       size = models.BigIntegerField()
       created_by = models.CharField(max_length=50)
       created_at = models.DateTimeField(auto_now_add=True)

       class Meta:
           ordering = ['-version_number']
           unique_together = ['file', 'version_number']

Thumbnail Model
^^^^^^^^^^^^^^^

.. code-block:: python

   class Thumbnail(models.Model):
       SIZE_CHOICES = [
           ('small', '128x128'),
           ('medium', '256x256'),
           ('large', '512x512'),
       ]

       id = models.UUIDField(primary_key=True, default=uuid.uuid4)
       file = models.ForeignKey(File, on_delete=models.CASCADE,
                               related_name='thumbnails')
       size_type = models.CharField(max_length=10, choices=SIZE_CHOICES)
       image = models.ImageField(upload_to=thumbnail_upload_path)
       width = models.IntegerField()
       height = models.IntegerField()
       created_at = models.DateTimeField(auto_now_add=True)

       class Meta:
           unique_together = ['file', 'size_type']

Indexes
-------

For optimal query performance:

.. code-block:: python

   class File(models.Model):
       class Meta:
           indexes = [
               models.Index(fields=['owner_id', 'folder']),
               models.Index(fields=['owner_id', 'is_trashed']),
               models.Index(fields=['owner_id', 'is_starred']),
               models.Index(fields=['created_at']),
           ]

   class Folder(models.Model):
       class Meta:
           indexes = [
               models.Index(fields=['owner_id', 'parent']),
               models.Index(fields=['owner_id', 'is_trashed']),
           ]

Query Examples
--------------

**List user's files in a folder:**

.. code-block:: python

   File.objects.filter(
       owner_id=user_id,
       folder=folder_id,
       is_trashed=False
   ).order_by('-created_at')

**Get folder contents (files + subfolders):**

.. code-block:: python

   files = File.objects.filter(owner_id=user_id, folder=folder_id)
   subfolders = Folder.objects.filter(owner_id=user_id, parent=folder_id)

**Calculate user's storage usage:**

.. code-block:: python

   from django.db.models import Sum, Count

   usage = File.objects.filter(owner_id=user_id).aggregate(
       total_size=Sum('size'),
       file_count=Count('id')
   )

**Find shares for a file:**

.. code-block:: python

   shares = Share.objects.filter(file=file_id).select_related('file')
