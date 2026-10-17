Storage Architecture
====================

Deployment examples use ``storage.example.invalid``, ``storage-node`` and
``/srv/shizuha-drive/minio`` as placeholders. Replace them with your reviewed
service endpoint, SSH host alias and service-owned storage directory. These
examples describe no particular private cluster. Credential placeholders must
be supplied securely through managed deployment configuration before use.


Shizuha Drive uses a two-tier storage architecture: **metadata** in PostgreSQL and **file content** in MinIO (S3-compatible object storage).

Storage Topology
----------------

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────────┐
   │   control-plane (K3s Control Plane)     S1 (K3s Worker Node)        │
   │                                                                  │
   │   ┌─────────────────────┐           ┌─────────────────────┐     │
   │   │   Drive Backend     │           │      MinIO          │     │
   │   │   (Django)          │           │                     │     │
   │   │                     │  HTTP/S3  │   NodePort: 30900   │     │
   │   │   ┌─────────────┐   │──────────▶│                     │     │
   │   │   │ django-     │   │           │   Data Path:        │     │
   │   │   │ storages    │   │           │   /srv/shizuha-drive/    │     │
   │   │   │ (S3Boto3)   │   │           │   minio-data/       │     │
   │   │   └─────────────┘   │           │   (~500GB)          │     │
   │   │                     │           │                     │     │
   │   └─────────────────────┘           └─────────────────────┘     │
   │                                                                  │
   │   ┌─────────────────────┐                                       │
   │   │    PostgreSQL       │                                       │
   │   │    (metadata)       │                                       │
   │   │    - File records   │                                       │
   │   │    - Folder tree    │                                       │
   │   │    - Share records  │                                       │
   │   │    - Quota usage    │                                       │
   │   └─────────────────────┘                                       │
   │                                                                  │
   └─────────────────────────────────────────────────────────────────┘

Why Two-Tier Storage?
---------------------

**Separation of Concerns:**

* **Metadata (PostgreSQL)**: Fast queries, relationships, transactions
* **File Content (MinIO)**: Scalable blob storage, S3-compatible

**Benefits:**

1. **Scalability**: Object storage scales independently
2. **Flexibility**: Can migrate to AWS S3, Cloudflare R2, etc.
3. **Performance**: Database queries remain fast regardless of file count
4. **Cost**: Object storage is cost-effective for large files

MinIO Configuration
-------------------

MinIO runs on the S1 node (physical server) to leverage its large disk capacity.

**Helm Values:**

.. code-block:: yaml

   minio:
     enabled: true
     nodeHostname: "storage-node"
     dataPath: "/srv/shizuha-drive/minio"
     rootUser: "<set-from-managed-configuration>"
     rootPassword: "<set-from-managed-configuration>"
     bucketName: "shizuha-drive"
     externalEndpoint: "http://storage.example.invalid:30900"
     service:
       s3NodePort: 30900
       consoleNodePort: 30901

**Cross-Node Access:**

Due to K3s Flannel networking limitations between WSL2 and physical nodes, Drive connects to MinIO via the external endpoint (S1's LAN IP + NodePort) instead of the Kubernetes ClusterIP service.

Django Storage Backend
----------------------

Drive uses django-storages with the S3Boto3Storage backend:

**Settings:**

.. code-block:: python

   USE_S3_STORAGE = os.environ.get('USE_S3_STORAGE', '0') == '1'

   if USE_S3_STORAGE:
       STORAGES = {
           "default": {
               "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
           },
       }
       AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
       AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
       AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME')
       AWS_S3_ENDPOINT_URL = os.environ.get('AWS_S3_ENDPOINT_URL')
       AWS_S3_SIGNATURE_VERSION = 's3v4'
       AWS_S3_ADDRESSING_STYLE = 'path'  # Required for MinIO

**Environment Variables:**

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Variable
     - Description
   * - ``USE_S3_STORAGE``
     - Enable S3 storage (``1`` or ``0``)
   * - ``AWS_S3_ENDPOINT_URL``
     - MinIO endpoint (e.g., ``http://storage.example.invalid:30900``)
   * - ``AWS_ACCESS_KEY_ID``
     - MinIO root user
   * - ``AWS_SECRET_ACCESS_KEY``
     - MinIO root password
   * - ``AWS_STORAGE_BUCKET_NAME``
     - Bucket name (e.g., ``shizuha-drive``)

File Storage Path
-----------------

Files are stored in MinIO with the following path structure:

.. code-block:: text

   files/{owner_id}/{year}/{month}/{uuid}_{filename}

**Example:**

.. code-block:: text

   files/3/2026/01/a1b2c3d4-e5f6-7890-abcd-ef1234567890_report.pdf

**Benefits:**

* Owner isolation by ID prefix
* Time-based partitioning for easier management
* UUID prevents filename collisions
* Original filename preserved for display

Bucket Structure
----------------

.. code-block:: text

   shizuha-drive/                    # Main bucket
   ├── files/                        # User files
   │   ├── 1/                        # User ID 1
   │   │   ├── 2026/01/              # January 2026
   │   │   │   ├── uuid_file1.pdf
   │   │   │   └── uuid_file2.jpg
   │   │   └── 2026/02/              # February 2026
   │   └── 2/                        # User ID 2
   ├── thumbnails/                   # Generated thumbnails
   │   ├── small/
   │   ├── medium/
   │   └── large/
   └── versions/                     # File version history

Fallback: Local Storage
-----------------------

When MinIO is disabled (``USE_S3_STORAGE=0``), Drive falls back to local filesystem storage:

.. code-block:: python

   MEDIA_URL = 'shizuha-drive/media/'
   MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

This is useful for:

* Local development without MinIO
* Single-node deployments
* Testing

Storage Operations
------------------

**Upload Flow:**

.. code-block:: python

   # In views.py
   from django.core.files.storage import default_storage

   # Save file (automatically goes to MinIO)
   path = default_storage.save(f'files/{owner_id}/{filename}', file)

   # Create database record
   File.objects.create(
       owner_id=owner_id,
       name=filename,
       file=path,
       size=file.size
   )

**Download Flow:**

.. code-block:: python

   # Generate presigned URL (for private files)
   from django.core.files.storage import default_storage

   url = default_storage.url(file_obj.file.name)
   # Returns: https://minio:9000/bucket/path?signature=...

**Delete Flow:**

.. code-block:: python

   # Delete from storage
   default_storage.delete(file_obj.file.name)

   # Delete database record
   file_obj.delete()

Capacity Planning
-----------------

**Current Setup:**

* S1 has ~500GB available at ``/srv/shizuha-drive/minio/``
* With 5GB default quota, supports ~100 users
* With 100GB premium quota, supports ~5 heavy users

**Scaling Options:**

1. **Add disk to S1**: Expand the hostPath volume
2. **Distributed MinIO**: Add more nodes for replication
3. **External S3**: Migrate to AWS S3, Cloudflare R2, or Backblaze B2

Backup Strategy
---------------

MinIO data can be backed up using:

.. code-block:: bash

   # Mirror to backup location
   mc mirror local/shizuha-drive /backup/shizuha-drive

   # Or rsync the data directory
   rsync -av /srv/shizuha-drive/minio/ /backup/minio-data/

For production, consider:

* MinIO's built-in versioning
* Scheduled backup jobs
* Cross-region replication
