Contributing
============

Thank you for your interest in contributing to Shizuha Drive! This guide will help you get started.

Getting Started
---------------

1. Fork the Repository
^^^^^^^^^^^^^^^^^^^^^^

Fork the shizuha-stack repository on GitHub.

2. Clone Your Fork
^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   git clone https://github.com/YOUR-USERNAME/shizuha-stack.git
   cd cli-stack
   git submodule update --init --recursive

3. Set Up Development Environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   # Start all services
   docker compose up -d

   # Verify Drive is running
   docker compose logs -f shizuha-drive

4. Create a Branch
^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   git checkout -b feature/your-feature-name

Making Changes
--------------

Code Guidelines
^^^^^^^^^^^^^^^

**Python (Backend)**:

* Follow PEP 8 style guidelines
* Use type hints for function signatures
* Write docstrings for public functions
* Keep functions focused and small

**TypeScript (Frontend)**:

* Use functional components with hooks
* Follow React best practices
* Use TypeScript strictly (no ``any``)
* Keep components small and reusable

Commit Messages
^^^^^^^^^^^^^^^

Use conventional commit format:

.. code-block:: text

   type(scope): description

   [optional body]

   [optional footer]

Types:

* ``feat``: New feature
* ``fix``: Bug fix
* ``docs``: Documentation changes
* ``style``: Code style changes (formatting, etc.)
* ``refactor``: Code refactoring
* ``test``: Adding or updating tests
* ``chore``: Maintenance tasks

Examples:

.. code-block:: text

   feat(drive): add folder sharing functionality

   fix(drive): resolve file download 404 error

   docs(drive): update API documentation

Testing Your Changes
--------------------

Backend Tests
^^^^^^^^^^^^^

.. code-block:: bash

   # Run all Drive tests
   docker compose exec shizuha-drive python manage.py test

   # Run specific test
   docker compose exec shizuha-drive python manage.py test drive.tests.test_files

   # Run with coverage
   docker compose exec shizuha-drive coverage run manage.py test
   docker compose exec shizuha-drive coverage report

Frontend Tests
^^^^^^^^^^^^^^

.. code-block:: bash

   cd drive/frontend

   # Run unit tests
   npm test

   # Run with coverage
   npm run test:coverage

E2E Tests
^^^^^^^^^

.. code-block:: bash

   # Run Playwright tests
   docker compose -f docker-compose.test.yml up --abort-on-container-exit

Submitting a Pull Request
-------------------------

1. Push Your Changes
^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   git push origin feature/your-feature-name

2. Open a Pull Request
^^^^^^^^^^^^^^^^^^^^^^

* Go to the repository on GitHub
* Click "New Pull Request"
* Select your branch
* Fill out the PR template

3. PR Checklist
^^^^^^^^^^^^^^^

Before submitting, ensure:

* [ ] Tests pass locally
* [ ] Code follows style guidelines
* [ ] Documentation updated if needed
* [ ] Commit messages follow conventions
* [ ] No sensitive data in commits

4. Code Review
^^^^^^^^^^^^^^

* Respond to review comments promptly
* Make requested changes in new commits
* Request re-review when ready

Reporting Issues
----------------

Bug Reports
^^^^^^^^^^^

Include:

* Clear description of the issue
* Steps to reproduce
* Expected vs actual behavior
* Environment details (OS, browser, versions)
* Error messages and logs

Feature Requests
^^^^^^^^^^^^^^^^

Include:

* Clear description of the feature
* Use case / motivation
* Proposed solution (if any)
* Alternatives considered

Development Tips
----------------

Useful Commands
^^^^^^^^^^^^^^^

.. code-block:: bash

   # Access Django shell
   docker compose exec shizuha-drive python manage.py shell

   # Create migrations
   docker compose exec shizuha-drive python manage.py makemigrations

   # Apply migrations
   docker compose exec shizuha-drive python manage.py migrate

   # Restart Drive backend
   docker compose restart shizuha-drive

   # View real-time logs
   docker compose logs -f shizuha-drive

Debugging
^^^^^^^^^

Enable debug logging:

.. code-block:: bash

   # In docker-compose.yml or environment
   DEBUG=1
   LOG_LEVEL=DEBUG

Database Access
^^^^^^^^^^^^^^^

.. code-block:: bash

   # Access database directly
   docker compose exec postgres psql -U postgres -d shizuha_drive

   # Django database shell
   docker compose exec shizuha-drive python manage.py dbshell

Getting Help
------------

* Check existing issues and documentation
* Ask in project discussions
* Join the community chat (if available)

Thank you for contributing!
