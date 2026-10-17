Administration Guide
====================

Deployment examples use ``storage.example.invalid``, ``storage-node`` and
``/srv/shizuha-drive/minio`` as placeholders. Replace them with your reviewed
service endpoint, SSH host alias and service-owned storage directory. These
examples describe no particular private cluster. Credential placeholders must
be supplied securely through managed deployment configuration before use.


This guide covers deployment, configuration, and operations for Shizuha Drive administrators.

Overview
--------

Shizuha Drive is deployed as part of the Shizuha Platform using Kubernetes (K3s) and Helm charts.

**Key Components:**

* **Drive Backend** - Django application (port 8018)
* **Drive Frontend** - React SPA (port 5182)
* **MinIO** - Object storage (port 30900)
* **PostgreSQL** - Metadata database
* **Redis** - Celery task broker

Quick Reference
---------------

**Access Points:**

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Service
     - URL
   * - Drive Web UI
     - ``https://your-domain/drive/``
   * - Drive API
     - ``https://your-domain/shizuha-drive/api/``
   * - MinIO Console
     - ``http://storage.example.invalid:30901`` (internal)
   * - MinIO S3 API
     - ``http://storage.example.invalid:30900`` (internal)

**Deployment credentials:**

No shared default credentials are provided. Retrieve the configured MinIO
administrator identity from your deployment's managed configuration. Supply
``MC_HOST_local`` securely in the operator environment for examples that pass
it to the MinIO client; never paste its value into documentation or source.

**Common Commands:**

.. code-block:: bash

   # Check Drive pod status
   kubectl -n shizuha get pods -l app.kubernetes.io/component=shizuha-drive

   # View Drive logs
   kubectl -n shizuha logs deploy/shizuha-shizuha-drive -f

   # Restart Drive
   kubectl -n shizuha rollout restart deploy/shizuha-shizuha-drive

   # Set user quota
   ./scripts/drive-set-quota.sh user@example.com 100

   # List all quotas
   ./scripts/drive-list-quotas.sh

Contents
--------

.. toctree::
   :maxdepth: 2

   deployment
   minio
   quotas
   troubleshooting
