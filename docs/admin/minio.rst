MinIO Administration
====================

Deployment examples use ``storage.example.invalid``, ``storage-node`` and
``/srv/shizuha-drive/minio`` as placeholders. Replace them with your reviewed
service endpoint, SSH host alias and service-owned storage directory. These
examples describe no particular private cluster. Credential placeholders must
be supplied securely through managed deployment configuration before use.


MinIO provides S3-compatible object storage for Shizuha Drive.

Architecture
------------

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────────┐
   │   Drive Backend                     MinIO (S1 Node)             │
   │   (control-plane)                       (storage-node)    │
   │                                                                  │
   │   ┌─────────────────┐               ┌─────────────────────┐     │
   │   │ Django          │   HTTP/S3     │ MinIO Server        │     │
   │   │ django-storages │──────────────▶│ Port 9000           │     │
   │   │ boto3           │               │                     │     │
   │   └─────────────────┘               │ Data:               │     │
   │                                     │ /srv/shizuha-drive/      │     │
   │   Browser                           │ minio-data/         │     │
   │   ┌─────────────────┐               │ (~500GB)            │     │
   │   │ MinIO Console   │──────────────▶│ Port 9001           │     │
   │   │                 │ NodePort      │                     │     │
   │   └─────────────────┘ 30901         └─────────────────────┘     │
   │                                                                  │
   └─────────────────────────────────────────────────────────────────┘

Accessing MinIO Console
-----------------------

The MinIO Console provides a web interface for managing buckets and objects.

**URL:** ``http://storage.example.invalid:30901``

**Credentials:** use the administrator identity supplied through your
managed deployment configuration. No common password is bundled. Commands
using ``-e MC_HOST_local`` require that variable to have been securely supplied
in the operator environment; its value includes credentials and must not be
printed or committed.

Console Features
^^^^^^^^^^^^^^^^

* Browse buckets and objects
* Upload/download files
* Manage bucket policies
* View storage metrics
* Configure lifecycle rules

Command Line Access
-------------------

Use the MinIO Client (``mc``) for CLI operations.

**Setup (via SSH to S1):**

.. code-block:: bash

   ssh storage-node

   # Using Docker
   docker run --rm --network host \
     -e MC_HOST_local \
     minio/mc:latest <command>

   # Or install mc locally
   wget https://dl.min.io/client/mc/release/linux-amd64/mc
   chmod +x mc
   mc alias set local http://localhost:30900 minioadmin MinioSecurePass123!

Common Commands
^^^^^^^^^^^^^^^

.. code-block:: bash

   # List buckets
   mc ls local/

   # List bucket contents
   mc ls local/shizuha-drive/

   # Bucket info
   mc stat local/shizuha-drive

   # Disk usage
   mc du local/shizuha-drive/

   # Find large files
   mc find local/shizuha-drive --larger 100MB

   # Mirror/backup
   mc mirror local/shizuha-drive /backup/shizuha-drive

Bucket Management
-----------------

**Default Buckets:**

* ``shizuha-drive`` - Main file storage (private)
* ``shizuha-drive-thumbnails`` - Thumbnail images (public read)

**Create New Bucket:**

.. code-block:: bash

   mc mb local/new-bucket

**Set Bucket Policy:**

.. code-block:: bash

   # Make bucket public read
   mc anonymous set download local/bucket-name

   # Remove public access
   mc anonymous set none local/bucket-name

**View Bucket Policy:**

.. code-block:: bash

   mc anonymous get local/bucket-name

Storage Monitoring
------------------

**Check Disk Usage:**

.. code-block:: bash

   # On S1 node
   df -h /srv/shizuha-drive/minio/

   # Via mc
   mc admin info local/

**MinIO Health:**

.. code-block:: bash

   # Health check endpoint
   curl http://storage.example.invalid:30900/minio/health/live

   # Cluster info
   mc admin info local/

Kubernetes Monitoring
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   # Pod status
   kubectl -n shizuha get pod -l app.kubernetes.io/component=minio

   # Pod logs
   kubectl -n shizuha logs deploy/shizuha-minio -f

   # Resource usage
   kubectl -n shizuha top pod -l app.kubernetes.io/component=minio

Lifecycle Policies
------------------

Configure automatic object cleanup:

**Delete incomplete uploads after 7 days:**

.. code-block:: bash

   mc ilm rule add --expire-delete-marker \
     --noncurrent-expire-days 7 \
     local/shizuha-drive

**Transition old versions to cheaper storage:**

.. code-block:: json

   {
     "Rules": [
       {
         "ID": "expire-noncurrent",
         "Status": "Enabled",
         "NoncurrentVersionExpiration": {
           "NoncurrentDays": 30
         }
       }
     ]
   }

Backup and Recovery
-------------------

**Full Backup:**

.. code-block:: bash

   # Mirror to backup location
   mc mirror local/shizuha-drive /backup/shizuha-drive-$(date +%Y%m%d)

   # Or rsync the data directory
   rsync -av /srv/shizuha-drive/minio/ /backup/minio-backup/

**Restore from Backup:**

.. code-block:: bash

   # Stop MinIO
   kubectl -n shizuha scale deploy/shizuha-minio --replicas=0

   # Restore data
   rsync -av /backup/minio-backup/ /srv/shizuha-drive/minio/

   # Start MinIO
   kubectl -n shizuha scale deploy/shizuha-minio --replicas=1

**Point-in-Time Recovery:**

Enable versioning for objects:

.. code-block:: bash

   mc version enable local/shizuha-drive

Then restore previous versions:

.. code-block:: bash

   mc cp --version-id VERSION_ID local/shizuha-drive/file.txt .

Security
--------

**Change Root Password:**

1. Update ``values-local.yaml``:

   .. code-block:: yaml

      minio:
        rootPassword: "<set-from-managed-configuration>"

2. Redeploy:

   .. code-block:: bash

      helm upgrade shizuha . -n shizuha -f values-local.yaml

**Enable TLS:**

For production, configure TLS:

.. code-block:: yaml

   minio:
     tls:
       enabled: true
       certSecret: minio-tls-secret

Troubleshooting
---------------

**MinIO Won't Start:**

.. code-block:: bash

   # Check pod events
   kubectl -n shizuha describe pod -l app.kubernetes.io/component=minio

   # Check data directory permissions
   ssh storage-node
   ls -la /srv/shizuha-drive/minio/

**Connection Refused:**

.. code-block:: bash

   # Verify service is running
   kubectl -n shizuha get svc shizuha-minio-external

   # Test connectivity
   curl http://storage.example.invalid:30900/minio/health/live

**Bucket Not Found:**

.. code-block:: bash

   # List existing buckets
   mc ls local/

   # Recreate bucket
   mc mb local/shizuha-drive

Performance Tuning
------------------

**For high throughput:**

.. code-block:: yaml

   minio:
     resources:
       requests:
         cpu: 1000m
         memory: 2Gi
       limits:
         cpu: 4000m
         memory: 8Gi

**Enable erasure coding (distributed mode):**

For production with multiple nodes, configure distributed MinIO for data redundancy.
