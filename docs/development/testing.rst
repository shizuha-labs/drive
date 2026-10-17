Testing Guide
=============

Shizuha Drive uses a comprehensive testing strategy including unit tests, integration tests, and E2E tests.

Test Structure
--------------

.. code-block:: text

   shizuha-drive/
   ├── drive/
   │   └── tests/
   │       ├── __init__.py
   │       ├── test_files.py       # File API tests
   │       ├── test_folders.py     # Folder API tests
   │       ├── test_shares.py      # Share API tests
   │       ├── test_quota.py       # Quota tests
   │       └── test_tasks.py       # Celery task tests
   └── frontend/
       └── tests/
           ├── components/         # Component tests
           └── e2e/                # Playwright E2E tests

Running Tests
-------------

Backend Tests (Django)
^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   # Run all tests
   docker compose exec shizuha-drive python manage.py test

   # Run specific test module
   docker compose exec shizuha-drive python manage.py test drive.tests.test_files

   # Run specific test class
   docker compose exec shizuha-drive python manage.py test drive.tests.test_files.FileUploadTest

   # Run specific test method
   docker compose exec shizuha-drive python manage.py test drive.tests.test_files.FileUploadTest.test_upload_success

   # Run with verbosity
   docker compose exec shizuha-drive python manage.py test -v 2

Frontend Tests
^^^^^^^^^^^^^^

.. code-block:: bash

   cd drive/frontend

   # Run unit tests
   npm test

   # Run in watch mode
   npm run test:watch

   # Run with coverage
   npm run test:coverage

E2E Tests (Playwright)
^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   # Using docker-compose
   docker compose -f docker-compose.test.yml up --abort-on-container-exit

   # Or locally (if Playwright installed)
   cd frontend && npx playwright test

Test Coverage
-------------

Backend Coverage
^^^^^^^^^^^^^^^^

.. code-block:: bash

   # Run with coverage
   docker compose exec shizuha-drive coverage run manage.py test

   # View report
   docker compose exec shizuha-drive coverage report

   # Generate HTML report
   docker compose exec shizuha-drive coverage html

   # Coverage output will be in htmlcov/

Frontend Coverage
^^^^^^^^^^^^^^^^^

.. code-block:: bash

   cd frontend
   npm run test:coverage

   # Coverage output will be in coverage/

Writing Tests
-------------

Backend Test Example
^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from django.test import TestCase
   from rest_framework.test import APITestCase
   from rest_framework import status
   from drive.models import File, Folder

   class FileUploadTest(APITestCase):
       """Tests for file upload functionality."""

       def setUp(self):
           """Set up test fixtures."""
           self.user_id = "test-user-uuid"
           # Create auth token
           self.client.credentials(
               HTTP_AUTHORIZATION=f'Bearer {self.get_test_token()}'
           )

       def test_upload_success(self):
           """Test successful file upload."""
           from django.core.files.uploadedfile import SimpleUploadedFile

           file = SimpleUploadedFile(
               "test.txt",
               b"Hello, World!",
               content_type="text/plain"
           )

           response = self.client.post(
               '/api/files/',
               {'file': file, 'name': 'test.txt'},
               format='multipart'
           )

           self.assertEqual(response.status_code, status.HTTP_201_CREATED)
           self.assertEqual(response.data['name'], 'test.txt')
           self.assertTrue(File.objects.filter(name='test.txt').exists())

       def test_upload_exceeds_quota(self):
           """Test upload fails when quota exceeded."""
           # Set small quota
           StorageQuota.objects.create(
               owner_id=self.user_id,
               max_bytes=100,
               used_bytes=50
           )

           # Try to upload large file
           file = SimpleUploadedFile(
               "large.txt",
               b"x" * 200,
               content_type="text/plain"
           )

           response = self.client.post(
               '/api/files/',
               {'file': file, 'name': 'large.txt'},
               format='multipart'
           )

           self.assertEqual(response.status_code, status.HTTP_507_INSUFFICIENT_STORAGE)

Frontend Test Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: typescript

   import { render, screen, fireEvent } from '@testing-library/react';
   import { FileUpload } from '../components/FileUpload';

   describe('FileUpload', () => {
     it('shows upload button', () => {
       render(<FileUpload onUpload={jest.fn()} />);
       expect(screen.getByText('Upload File')).toBeInTheDocument();
     });

     it('calls onUpload when file selected', async () => {
       const onUpload = jest.fn();
       render(<FileUpload onUpload={onUpload} />);

       const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
       const input = screen.getByTestId('file-input');

       fireEvent.change(input, { target: { files: [file] } });

       expect(onUpload).toHaveBeenCalledWith(file);
     });
   });

E2E Test Example
^^^^^^^^^^^^^^^^

.. code-block:: typescript

   import { test, expect } from '@playwright/test';

   test.describe('File Management', () => {
     test.beforeEach(async ({ page }) => {
       // Login before each test
       await page.goto('/id/login');
       await page.fill('[name="email"]', 'test@example.com');
       await page.fill('[name="password"]', 'password');
       await page.click('button[type="submit"]');
       await page.waitForURL('/drive/');
     });

     test('upload file', async ({ page }) => {
       // Upload a file
       const fileInput = page.locator('input[type="file"]');
       await fileInput.setInputFiles('test-files/sample.txt');

       // Wait for upload to complete
       await expect(page.locator('text=sample.txt')).toBeVisible();
     });

     test('create folder', async ({ page }) => {
       // Click new folder button
       await page.click('button:has-text("New Folder")');

       // Enter folder name
       await page.fill('[name="folderName"]', 'My Folder');
       await page.click('button:has-text("Create")');

       // Verify folder appears
       await expect(page.locator('text=My Folder')).toBeVisible();
     });

     test('download file', async ({ page }) => {
       // Navigate to a file
       await page.click('text=sample.txt');

       // Click download
       const [download] = await Promise.all([
         page.waitForEvent('download'),
         page.click('button:has-text("Download")'),
       ]);

       expect(download.suggestedFilename()).toBe('sample.txt');
     });
   });

Test Fixtures
-------------

Creating Test Data
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from django.test import TestCase
   from drive.models import File, Folder, StorageQuota

   class DriveTestCase(TestCase):
       fixtures = ['test_data.json']

       @classmethod
       def setUpTestData(cls):
           """Set up data for the whole TestCase."""
           cls.user_id = "test-user-uuid"

           # Create root folder
           cls.root_folder = Folder.objects.create(
               name="Root",
               owner_id=cls.user_id
           )

           # Create test files
           cls.test_file = File.objects.create(
               name="test.txt",
               owner_id=cls.user_id,
               folder=cls.root_folder,
               size=1024
           )

           # Create quota
           cls.quota = StorageQuota.objects.create(
               owner_id=cls.user_id,
               max_bytes=10 * 1024 * 1024 * 1024,  # 10 GB
               used_bytes=1024
           )

Mocking External Services
^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from unittest.mock import patch, MagicMock

   class StorageTest(TestCase):
       @patch('django.core.files.storage.default_storage')
       def test_file_delete(self, mock_storage):
           """Test file deletion from storage."""
           mock_storage.delete = MagicMock()

           file = File.objects.create(
               name="test.txt",
               owner_id="user-123",
               file="files/test.txt"
           )
           file.delete()

           mock_storage.delete.assert_called_once_with("files/test.txt")

Continuous Integration
----------------------

Tests run automatically on CI for every pull request:

.. code-block:: yaml

   # .github/workflows/test.yml
   name: Tests

   on: [push, pull_request]

   jobs:
     backend:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v3
         - name: Run tests
           run: |
             docker compose up -d
             docker compose exec -T shizuha-drive python manage.py test

     frontend:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v3
         - name: Install dependencies
           run: cd drive/frontend && npm ci
         - name: Run tests
           run: cd drive/frontend && npm test

     e2e:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v3
         - name: Run E2E tests
           run: docker compose -f docker-compose.test.yml up --abort-on-container-exit

Best Practices
--------------

1. **Isolation**: Each test should be independent
2. **Cleanup**: Use ``setUp`` and ``tearDown`` for test data
3. **Mocking**: Mock external services (S3, email, etc.)
4. **Descriptive names**: Test names should describe expected behavior
5. **Fast tests**: Keep unit tests fast; use integration tests sparingly
6. **Coverage**: Aim for 80%+ code coverage

Explicit runtime credentials
----------------------------

Production settings require nonblank ``SECRET_KEY`` and ``POSTGRES_PASSWORD``.
Configured bytes are preserved. An explicitly supplied ``JWT_SECRET_KEY`` must
also be nonblank; when omitted, the existing installation ``SECRET_KEY`` fallback
is retained. No database password is invented by application settings.

When ``USE_S3_STORAGE=1``, both ``AWS_ACCESS_KEY_ID`` and
``AWS_SECRET_ACCESS_KEY`` must be supplied explicitly. Local filesystem mode does
not require inactive S3 credentials and does not discover an ambient S3 identity.

The deploy test gate configures its isolated PostgreSQL service explicitly.
Static collection and the offline migration-state gate generate disposable
credentials for that command only; these values are not image environment
variables or production defaults. Local PostgreSQL tests must use credentials
that match their own disposable database.
