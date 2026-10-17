Installation
============

This guide covers installing Shizuha Drive in various environments.

Prerequisites
-------------

Before installing Shizuha Drive, ensure you have:

* Python 3.11+
* PostgreSQL 14+
* Redis 7+
* MinIO or S3-compatible storage (optional, for production)
* Node.js 20+ (for frontend)

Docker Installation (Recommended)
---------------------------------

The easiest way to run Shizuha Drive is using Docker Compose as part of the Shizuha Stack.

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/your-org/shizuha-stack.git
   cd cli-stack

   # Start all services
   docker compose up -d

   # View logs
   docker compose logs -f shizuha-drive

Kubernetes Installation
-----------------------

For production deployments, use the Helm chart:

.. code-block:: bash

   # Add Helm repo (if applicable)
   helm repo add shizuha https://charts.shizuha.io

   # Install with default values
   helm install shizuha shizuha/shizuha-stack

   # Or with custom values
   helm install shizuha shizuha/shizuha-stack -f my-values.yaml

See :doc:`admin/deployment` for detailed Kubernetes deployment instructions.

Manual Installation
-------------------

For development or custom setups:

Backend Setup
^^^^^^^^^^^^^

.. code-block:: bash

   cd cli-drive

   # Create virtual environment
   python -m venv venv
   source venv/bin/activate

   # Install dependencies
   pip install uv==0.11.26
   uv sync --locked --no-dev --no-install-project --no-editable

   # Configure environment
   cp .env.example .env
   # Edit .env with your settings

   # Run migrations
   python manage.py migrate

   # Create superuser (optional)
   python manage.py createsuperuser

   # Start development server
   python manage.py runserver 0.0.0.0:8018

Frontend Setup
^^^^^^^^^^^^^^

.. code-block:: bash

   cd drive/frontend

   # Install dependencies
   npm install

   # Start development server
   npm run dev

Environment Variables
---------------------

Required environment variables:

.. list-table::
   :header-rows: 1
   :widths: 30 50 20

   * - Variable
     - Description
     - Default
   * - ``POSTGRES_HOST``
     - PostgreSQL host
     - ``localhost``
   * - ``POSTGRES_PORT``
     - PostgreSQL port
     - ``5432``
   * - ``POSTGRES_DB``
     - Database name
     - ``shizuha_drive``
   * - ``POSTGRES_USER``
     - Database user
     - ``postgres``
   * - ``POSTGRES_PASSWORD``
     - Database password
     - (required)
   * - ``REDIS_URL``
     - Redis connection URL
     - ``redis://localhost:6379/8``
   * - ``JWT_SECRET``
     - JWT signing secret
     - (required)
   * - ``USE_S3_STORAGE``
     - Enable S3/MinIO storage
     - ``0``

For S3/MinIO storage, also set:

.. list-table::
   :header-rows: 1
   :widths: 30 50 20

   * - Variable
     - Description
     - Default
   * - ``AWS_ACCESS_KEY_ID``
     - S3 access key
     - (required)
   * - ``AWS_SECRET_ACCESS_KEY``
     - S3 secret key
     - (required)
   * - ``AWS_STORAGE_BUCKET_NAME``
     - S3 bucket name
     - ``shizuha-drive``
   * - ``AWS_S3_ENDPOINT_URL``
     - S3/MinIO endpoint
     - (required for MinIO)

Next Steps
----------

* :doc:`configuration` - Detailed configuration options
* :doc:`quickstart` - Upload your first file
* :doc:`admin/minio` - Set up MinIO storage
