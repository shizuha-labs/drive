Development Guide
=================

This section covers development setup and contribution guidelines for Shizuha Drive.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   contributing
   testing
   api-development

Development Environment
-----------------------

Prerequisites
^^^^^^^^^^^^^

* Python 3.11+
* Node.js 20+
* Docker and Docker Compose
* Git

Quick Setup
^^^^^^^^^^^

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/your-org/shizuha-stack.git
   cd cli-stack

   # Start development environment
   docker compose up -d

   # View Drive logs
   docker compose logs -f shizuha-drive

DevMode (K3s)
^^^^^^^^^^^^^

For K3s development with hot reload:

.. code-block:: yaml

   # values-local.yaml
   global:
     devMode:
       enabled: true
       codePath: "/path/to/shizuha-stack"
       nodeHostname: "your-dev-node"

This mounts source code into pods for live editing:

* **Frontend**: Full HMR (Hot Module Reload) - changes appear instantly
* **Backend**: Requires manual restart after changes:

  .. code-block:: bash

     kubectl exec -n shizuha deploy/shizuha-shizuha-drive -- supervisorctl restart django

Project Structure
-----------------

.. code-block:: text

   shizuha-drive/
   ├── drivemanager/          # Django project settings
   │   ├── settings.py        # Main settings
   │   ├── urls.py            # URL routing
   │   └── celery.py          # Celery configuration
   ├── drive/                  # Main Django app
   │   ├── models.py          # Database models
   │   ├── views.py           # API views
   │   ├── serializers.py     # DRF serializers
   │   ├── tasks.py           # Celery tasks
   │   └── tests/             # Test files
   ├── frontend/               # React frontend
   │   ├── src/
   │   │   ├── components/    # React components
   │   │   ├── pages/         # Page components
   │   │   ├── hooks/         # Custom React hooks
   │   │   └── api/           # API client
   │   └── package.json
   ├── docs/                   # Sphinx documentation
   ├── scripts/                # Utility scripts
   └── pyproject.toml / uv.lock  # Python dependencies

Code Style
----------

Backend (Python)
^^^^^^^^^^^^^^^^

* Follow PEP 8 guidelines
* Use Black for formatting
* Use isort for import sorting
* Use type hints where practical

.. code-block:: bash

   # Format code
   black .
   isort .

   # Check linting
   flake8

Frontend (TypeScript/React)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

* Use ESLint and Prettier
* Follow React best practices
* Use TypeScript for type safety

.. code-block:: bash

   cd frontend
   npm run lint
   npm run format

Database Migrations
-------------------

Creating Migrations
^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   # Generate migration
   docker compose exec shizuha-drive python manage.py makemigrations

   # Apply migrations
   docker compose exec shizuha-drive python manage.py migrate

   # Show migration status
   docker compose exec shizuha-drive python manage.py showmigrations

Migration Best Practices
^^^^^^^^^^^^^^^^^^^^^^^^

1. Always review generated migrations before committing
2. Use meaningful migration names with ``--name``
3. Test migrations on a copy of production data
4. Never edit migrations that have been applied in production

API Development
---------------

See :doc:`api-development` for detailed API development guidelines.

Debugging
---------

Django Debug
^^^^^^^^^^^^

Enable debug mode:

.. code-block:: bash

   DEBUG=1

View SQL queries:

.. code-block:: python

   from django.db import connection
   print(connection.queries)

Celery Debug
^^^^^^^^^^^^

Run Celery with verbose logging:

.. code-block:: bash

   celery -A drivemanager worker -l DEBUG

Remote Debugging
^^^^^^^^^^^^^^^^

For VSCode remote debugging, add to launch.json:

.. code-block:: json

   {
     "name": "Django Remote",
     "type": "python",
     "request": "attach",
     "connect": {
       "host": "localhost",
       "port": 5678
     }
   }
