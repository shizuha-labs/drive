Troubleshooting Guide
=====================

Deployment examples use ``storage.example.invalid``, ``storage-node`` and
``/srv/shizuha-drive/minio`` as placeholders. Replace them with your reviewed
service endpoint, SSH host alias and service-owned storage directory. These
examples describe no particular private cluster. Credential placeholders must
be supplied securely through managed deployment configuration before use.


Common issues and solutions for Shizuha Drive.

Quick Diagnostics
-----------------

**Check All Components:**

.. code-block:: bash

   # Pod status
   kubectl -n shizuha get pods | grep -E 'drive|minio'

   # Expected output:
   # shizuha-minio-595456d847-xxxxx               1/1     Running
   # shizuha-shizuha-drive-58b6b6c95-xxxxx        1/1     Running
   # shizuha-shizuha-drive-frontend-xxxxx-xxxxx   1/1     Running

**Check Logs:**

.. code-block:: bash

   # Drive backend
   kubectl -n shizuha logs deploy/shizuha-shizuha-drive --tail=100

   # MinIO
   kubectl -n shizuha logs deploy/shizuha-minio --tail=100

   # Frontend
   kubectl -n shizuha logs deploy/shizuha-shizuha-drive-frontend --tail=100

Common Issues
-------------

Drive Pod Won't Start
^^^^^^^^^^^^^^^^^^^^^

**Symptoms:** Pod stuck in ``CrashLoopBackOff`` or ``Error``

**Check:**

.. code-block:: bash

   kubectl -n shizuha describe pod -l app.kubernetes.io/component=shizuha-drive
   kubectl -n shizuha logs deploy/shizuha-shizuha-drive --previous

**Common Causes:**

1. **Database connection failed:**

   .. code-block:: text

      Waiting for PostgreSQL...
      PostgreSQL not ready...

   **Fix:** Ensure PostgreSQL is running:

   .. code-block:: bash

      kubectl -n shizuha get pod -l app.kubernetes.io/name=postgresql

2. **Missing environment variables:**

   .. code-block:: text

      KeyError: 'POSTGRES_HOST'

   **Fix:** Check Helm values and secrets

3. **MinIO connection failed:**

   .. code-block:: text

      botocore.exceptions.EndpointConnectionError

   **Fix:** Verify MinIO is running and endpoint is correct

File Upload Fails
^^^^^^^^^^^^^^^^^

**Symptoms:** HTTP 500, 507, or timeout on upload

**Possible Causes:**

1. **Quota exceeded (507):**

   .. code-block:: json

      {"detail": "Storage quota exceeded"}

   **Fix:** Increase user quota or have user free space

2. **MinIO unreachable:**

   **Test connectivity:**

   .. code-block:: bash

      kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
        python -c "import socket; socket.create_connection(('storage.example.invalid', 30900), timeout=5)"

3. **File too large:**

   Default max upload is 100MB. Check Django settings.

4. **Bucket doesn't exist:**

   .. code-block:: bash

      # Create bucket
      ssh storage-node
      docker run --rm --network host \
        -e MC_HOST_local \
        minio/mc:latest mb --ignore-existing local/shizuha-drive

File Download Fails
^^^^^^^^^^^^^^^^^^^

**Symptoms:** 404 or 500 on download

**Check file exists in MinIO:**

.. code-block:: bash

   # Get file path from database
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python manage.py shell -c "
   from drive.models import File
   f = File.objects.first()
   print(f.file.name)
   "

   # Check if file exists in MinIO
   ssh storage-node
   docker run --rm --network host \
     -e MC_HOST_local \
     minio/mc:latest stat local/shizuha-drive/<file-path>

MinIO Issues
^^^^^^^^^^^^

**MinIO Pod Not Starting:**

.. code-block:: bash

   kubectl -n shizuha describe pod -l app.kubernetes.io/component=minio

**Common issues:**

1. **Node not found:** MinIO is pinned to S1 node via nodeSelector

   .. code-block:: bash

      kubectl get nodes
      # Verify storage-node exists

2. **Data directory permissions:**

   .. code-block:: bash

      ssh storage-node
      ls -la /srv/shizuha-drive/minio/
      # Should be writable by root (MinIO runs as root in container)

**MinIO Health Check Fails:**

.. code-block:: bash

   curl http://storage.example.invalid:30900/minio/health/live
   # Should return 200

Cross-Node Networking
^^^^^^^^^^^^^^^^^^^^^

**Symptoms:** Drive can't reach MinIO service

This is a known issue with K3s Flannel between WSL2 and physical nodes.

**Verify:**

.. code-block:: bash

   # From Drive pod, test MinIO connectivity
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python -c "
   import socket
   try:
       # This should fail (ClusterIP)
       socket.create_connection(('shizuha-minio', 9000), timeout=3)
       print('ClusterIP: OK')
   except:
       print('ClusterIP: FAILED')

   try:
       # This should work (NodePort via LAN IP)
       socket.create_connection(('storage.example.invalid', 30900), timeout=3)
       print('NodePort: OK')
   except:
       print('NodePort: FAILED')
   "

**Fix:** Use ``externalEndpoint`` in Helm values:

.. code-block:: yaml

   minio:
     externalEndpoint: "http://storage.example.invalid:30900"

Authentication Issues
^^^^^^^^^^^^^^^^^^^^^

**Symptoms:** 401 Unauthorized on API calls

**Check JWT token:**

1. Verify token is valid (not expired)
2. Verify JWT secret matches between Shizuha ID and Drive

.. code-block:: bash

   # Check secrets
   kubectl -n shizuha get secret shizuha-secrets -o yaml

   # Decode JWT secret
   kubectl -n shizuha get secret shizuha-secrets -o jsonpath='{.data.jwt-secret}' | base64 -d

Celery Task Issues
^^^^^^^^^^^^^^^^^^

**Tasks not processing:**

.. code-block:: bash

   # Check Celery worker
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     supervisorctl status

   # Should show:
   # celery-beat    RUNNING
   # celery-worker  RUNNING

**Check Redis connection:**

.. code-block:: bash

   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python -c "
   import redis
   r = redis.from_url('redis://shizuha-redis-master:6379/8')
   print('Redis connected:', r.ping())
   "

Database Issues
^^^^^^^^^^^^^^^

**Migration errors:**

.. code-block:: bash

   # Check migration status
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python manage.py showmigrations

   # Run migrations
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python manage.py migrate

**Database connection:**

.. code-block:: bash

   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python -c "
   from django.db import connection
   connection.ensure_connection()
   print('Database connected!')
   "

Log Analysis
------------

**Enable Debug Logging:**

.. code-block:: yaml

   shizuhaDrive:
     env:
       DEBUG: "1"
       LOG_LEVEL: "DEBUG"

**Django Error Tracking:**

.. code-block:: bash

   # View Django errors
   kubectl -n shizuha logs deploy/shizuha-shizuha-drive | grep -i error

   # View full traceback
   kubectl -n shizuha logs deploy/shizuha-shizuha-drive | grep -A 20 "Traceback"

Recovery Procedures
-------------------

**Restart Drive:**

.. code-block:: bash

   kubectl -n shizuha rollout restart deploy/shizuha-shizuha-drive

**Restart MinIO:**

.. code-block:: bash

   kubectl -n shizuha rollout restart deploy/shizuha-minio

**Reset Drive Database:**

.. warning::

   This deletes all file metadata!

.. code-block:: bash

   # Delete all files from database (not storage)
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python manage.py shell -c "
   from drive.models import File, Folder, Share, StorageQuota
   File.objects.all().delete()
   Folder.objects.all().delete()
   Share.objects.all().delete()
   StorageQuota.objects.all().delete()
   "

Getting Help
------------

1. Check logs first
2. Search existing issues
3. Collect diagnostics:

   .. code-block:: bash

      kubectl -n shizuha get pods -o wide
      kubectl -n shizuha describe pod <pod-name>
      kubectl -n shizuha logs <pod-name> --tail=200

4. Report issue with:

   * K3s/Kubernetes version
   * Helm chart version
   * Error messages
   * Steps to reproduce
