API Reference
=============

Shizuha Drive provides a RESTful API for managing files, folders, shares, and user storage quotas.

Base URL
--------

All API endpoints are relative to::

    https://your-domain/shizuha-drive/api/

Authentication
--------------

All API requests require a valid JWT token from Shizuha ID in the ``Authorization`` header::

    Authorization: Bearer <your-jwt-token>

See :doc:`authentication` for details on obtaining tokens.

API Endpoints Overview
----------------------

.. list-table::
   :header-rows: 1
   :widths: 30 10 60

   * - Endpoint
     - Methods
     - Description
   * - ``/files/``
     - GET, POST
     - List files, upload new files
   * - ``/files/{id}/``
     - GET, PATCH, DELETE
     - Get, update, or delete a file
   * - ``/files/{id}/download/``
     - GET
     - Download file content
   * - ``/files/{id}/move/``
     - POST
     - Move file to another folder
   * - ``/files/{id}/copy/``
     - POST
     - Copy file to another folder
   * - ``/folders/``
     - GET, POST
     - List folders, create new folder
   * - ``/folders/{id}/``
     - GET, PATCH, DELETE
     - Get, update, or delete a folder
   * - ``/folders/{id}/contents/``
     - GET
     - List folder contents (files + subfolders)
   * - ``/shares/``
     - GET, POST
     - List shares, create new share
   * - ``/shares/{id}/``
     - GET, PATCH, DELETE
     - Get, update, or revoke a share
   * - ``/shares/public/{token}/``
     - GET
     - Access public share (no auth required)
   * - ``/quota/``
     - GET
     - Get storage quota and usage
   * - ``/trash/``
     - GET
     - List trashed items
   * - ``/trash/empty/``
     - POST
     - Empty trash (permanent delete)
   * - ``/trash/restore/``
     - POST
     - Restore items from trash

Response Format
---------------

All responses are JSON with the following structure:

**Success Response:**

.. code-block:: javascript

   {
     "id": "uuid",
     "name": "example.pdf",
     "size": 1024
     // ... additional fields
   }

**Paginated Response:**

.. code-block:: javascript

   {
     "count": 100,
     "next": "https://api/files/?page=2",
     "previous": null,
     "results": []  // Array of items
   }

**Error Response:**

.. code-block:: json

   {
     "detail": "Error message here"
   }

HTTP Status Codes
-----------------

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Code
     - Description
   * - 200
     - Success
   * - 201
     - Resource created
   * - 204
     - Resource deleted (no content)
   * - 400
     - Bad request (validation error)
   * - 401
     - Unauthorized (missing/invalid token)
   * - 403
     - Forbidden (insufficient permissions)
   * - 404
     - Resource not found
   * - 413
     - File too large
   * - 507
     - Insufficient storage (quota exceeded)

Rate Limiting
-------------

API requests are rate-limited to prevent abuse:

* **Authenticated users**: 1000 requests/hour
* **File uploads**: 100 uploads/hour
* **Downloads**: 500 downloads/hour

Contents
--------

.. toctree::
   :maxdepth: 2

   authentication
   files
   folders
   shares
   quota
