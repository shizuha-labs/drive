Deployment Guide
================

Deployment examples use ``storage.example.invalid``, ``storage-node`` and
``/srv/shizuha-drive/minio`` as placeholders. Replace them with your reviewed
service endpoint, SSH host alias and service-owned storage directory. These
examples describe no particular private cluster. Credential placeholders must
be supplied securely through managed deployment configuration before use.


Shizuha Drive is deployed using Helm charts as part of the Shizuha Stack.

Prerequisites
-------------

* K3s cluster (or Kubernetes 1.24+)
* Helm 3.x
* PostgreSQL database
* Redis for Celery
* MinIO for object storage (optional, can use local storage)

Helm Chart Configuration
------------------------

Drive is configured in the Shizuha Stack Helm values.

**values-local.yaml (Development):**

.. code-block:: yaml

   shizuhaDrive:
     enabled: true
     replicaCount: 1
     image:
       repository: docker.io/library/shizuha-drive
       tag: latest
     nodeSelector: {}
     resources:
       requests:
         cpu: 200m
         memory: 512Mi
       limits:
         cpu: 1000m
         memory: 2Gi
     env:
       DEBUG: "1"
     frontend:
       enabled: true
       image:
         repository: docker.io/library/shizuha-drive-frontend
         tag: latest

   minio:
     enabled: true
     nodeHostname: "storage-node"
     dataPath: "/srv/shizuha-drive/minio"
     rootUser: "<set-from-managed-configuration>"
     rootPassword: "<set-from-managed-configuration>"
     bucketName: "shizuha-drive"
     externalEndpoint: "http://storage.example.invalid:30900"

**values-production.yaml:**

.. code-block:: yaml

   shizuhaDrive:
     enabled: true
     replicaCount: 2
     image:
       repository: your-registry/shizuha-drive
       tag: v1.0.0
     resources:
       requests:
         cpu: 500m
         memory: 1Gi
       limits:
         cpu: 2000m
         memory: 4Gi
     env:
       DEBUG: "0"

   minio:
     enabled: true
     nodeHostname: "storage-node"
     dataPath: "/data/minio"
     rootUser: "<set-from-managed-configuration>"
     rootPassword: "<set-from-managed-configuration>"  # From sealed secret
     bucketName: "shizuha-drive"

Deployment Commands
-------------------

**Initial Deployment:**

.. code-block:: bash

   cd deploy/k3s/charts/shizuha-stack

   # Deploy with local values
   helm install shizuha . -n shizuha --create-namespace \
     -f values-local.yaml

**Upgrade Deployment:**

.. code-block:: bash

   helm upgrade shizuha . -n shizuha -f values-local.yaml

**Rollback:**

.. code-block:: bash

   # List revisions
   helm history shizuha -n shizuha

   # Rollback to previous
   helm rollback shizuha -n shizuha

   # Rollback to specific revision
   helm rollback shizuha 5 -n shizuha

Environment Variables
---------------------

The following environment variables are configured via Helm:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Variable
     - Description
   * - ``DEBUG``
     - Django debug mode (``0`` or ``1``)
   * - ``POSTGRES_HOST``
     - PostgreSQL hostname
   * - ``POSTGRES_PORT``
     - PostgreSQL port (5432)
   * - ``POSTGRES_DB``
     - Database name (``shizuha_drive``)
   * - ``POSTGRES_USER``
     - Database username
   * - ``POSTGRES_PASSWORD``
     - Database password (from secret)
   * - ``CELERY_BROKER_URL``
     - Redis URL for Celery
   * - ``JWT_SECRET_KEY``
     - Shared JWT secret (from secret)
   * - ``USE_S3_STORAGE``
     - Enable MinIO storage (``1``)
   * - ``AWS_S3_ENDPOINT_URL``
     - MinIO endpoint URL
   * - ``AWS_ACCESS_KEY_ID``
     - MinIO access key
   * - ``AWS_SECRET_ACCESS_KEY``
     - MinIO secret key
   * - ``AWS_STORAGE_BUCKET_NAME``
     - MinIO bucket name

Database Setup
--------------

The database is automatically created by PostgreSQL init scripts:

.. code-block:: sql

   CREATE DATABASE shizuha_drive;

**Run Migrations:**

.. code-block:: bash

   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python manage.py migrate

**Create Superuser (if needed):**

.. code-block:: bash

   kubectl -n shizuha exec -it deploy/shizuha-shizuha-drive -- \
     python manage.py createsuperuser

Health Checks
-------------

**Liveness Probe:**

.. code-block:: yaml

   livenessProbe:
     tcpSocket:
       port: 8018
     initialDelaySeconds: 30
     periodSeconds: 10

**Readiness Probe:**

.. code-block:: yaml

   readinessProbe:
     tcpSocket:
       port: 8018
     initialDelaySeconds: 5
     periodSeconds: 5

Scaling
-------

**Horizontal Scaling:**

.. code-block:: bash

   # Scale to 3 replicas
   kubectl -n shizuha scale deploy/shizuha-shizuha-drive --replicas=3

**Autoscaling:**

.. code-block:: yaml

   apiVersion: autoscaling/v2
   kind: HorizontalPodAutoscaler
   metadata:
     name: shizuha-drive-hpa
   spec:
     scaleTargetRef:
       apiVersion: apps/v1
       kind: Deployment
       name: shizuha-shizuha-drive
     minReplicas: 2
     maxReplicas: 10
     metrics:
       - type: Resource
         resource:
           name: cpu
           target:
             type: Utilization
             averageUtilization: 70

Resource Recommendations
------------------------

**Development:**

* CPU: 200m request, 1000m limit
* Memory: 512Mi request, 2Gi limit

**Production:**

* CPU: 500m request, 2000m limit
* Memory: 1Gi request, 4Gi limit

**High Traffic:**

* CPU: 1000m request, 4000m limit
* Memory: 2Gi request, 8Gi limit
