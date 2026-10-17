Shares API
==========

The Shares API allows you to share files and folders with other users or create public links.

List Shares
-----------

.. http:get:: /api/shares/

   List all shares created by the authenticated user.

   :query file: Filter by file ID
   :query folder: Filter by folder ID
   :query is_public: Filter public shares

   **Example Response:**

   .. code-block:: json

      {
        "count": 2,
        "results": [
          {
            "id": "550e8400-e29b-41d4-a716-446655440500",
            "file": "550e8400-e29b-41d4-a716-446655440001",
            "folder": null,
            "shared_by": 1,
            "shared_with": 2,
            "shared_with_email": "colleague@example.com",
            "permission": "VIEW",
            "is_public": false,
            "public_token": null,
            "expires_at": null,
            "created_at": "2026-01-20T10:00:00Z"
          },
          {
            "id": "550e8400-e29b-41d4-a716-446655440501",
            "file": "550e8400-e29b-41d4-a716-446655440002",
            "folder": null,
            "shared_by": 1,
            "shared_with": null,
            "shared_with_email": null,
            "permission": "VIEW",
            "is_public": true,
            "public_token": "abc123xyz",
            "public_url": "https://your-domain/shizuha-drive/s/abc123xyz",
            "expires_at": "2026-02-20T10:00:00Z",
            "created_at": "2026-01-25T14:30:00Z"
          }
        ]
      }

Create Share
------------

.. http:post:: /api/shares/

   Create a new share for a file or folder.

   :<json string file: File ID to share (required if no folder)
   :<json string folder: Folder ID to share (required if no file)
   :<json integer shared_with: User ID to share with (for user shares)
   :<json string shared_with_email: Email of user to share with
   :<json string permission: Permission level (``VIEW`` or ``EDIT``)
   :<json boolean is_public: Create a public link (default: false)
   :<json string password: Password protect the share (optional)
   :<json string expires_at: Expiration datetime (ISO 8601, optional)

   :statuscode 201: Share created

Share with User
^^^^^^^^^^^^^^^

.. code-block:: bash

   curl -X POST "https://your-domain/shizuha-drive/api/shares/" \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file": "550e8400-e29b-41d4-a716-446655440001",
       "shared_with_email": "colleague@example.com",
       "permission": "EDIT"
     }'

Create Public Link
^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   curl -X POST "https://your-domain/shizuha-drive/api/shares/" \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file": "550e8400-e29b-41d4-a716-446655440001",
       "is_public": true,
       "permission": "VIEW",
       "expires_at": "2026-02-28T23:59:59Z"
     }'

**Response:**

.. code-block:: json

   {
     "id": "550e8400-e29b-41d4-a716-446655440502",
     "file": "550e8400-e29b-41d4-a716-446655440001",
     "is_public": true,
     "public_token": "Kj8mN2pQ",
     "public_url": "https://your-domain/shizuha-drive/s/Kj8mN2pQ",
     "permission": "VIEW",
     "expires_at": "2026-02-28T23:59:59Z",
     "created_at": "2026-01-31T12:00:00Z"
   }

Password-Protected Share
^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   curl -X POST "https://your-domain/shizuha-drive/api/shares/" \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file": "550e8400-e29b-41d4-a716-446655440001",
       "is_public": true,
       "password": "secret123"
     }'

Get Share Details
-----------------

.. http:get:: /api/shares/(uuid:id)/

   Get details of a specific share.

   :param id: Share UUID

Update Share
------------

.. http:patch:: /api/shares/(uuid:id)/

   Update share settings.

   :param id: Share UUID
   :<json string permission: New permission level
   :<json string password: New password (or null to remove)
   :<json string expires_at: New expiration (or null for no expiry)

   **Example:**

   .. code-block:: bash

      curl -X PATCH "https://your-domain/shizuha-drive/api/shares/550e8400.../" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"permission": "VIEW", "expires_at": "2026-03-31T23:59:59Z"}'

Revoke Share
------------

.. http:delete:: /api/shares/(uuid:id)/

   Revoke/delete a share.

   :param id: Share UUID
   :statuscode 204: Share revoked

   **Example:**

   .. code-block:: bash

      curl -X DELETE "https://your-domain/shizuha-drive/api/shares/550e8400.../" \
        -H "Authorization: Bearer YOUR_TOKEN"

Access Public Share
-------------------

.. http:get:: /api/shares/public/(string:token)/

   Access a public share. No authentication required.

   :param token: Public share token
   :query password: Password if share is protected

   **Example:**

   .. code-block:: bash

      # Without password
      curl "https://your-domain/shizuha-drive/api/shares/public/Kj8mN2pQ/"

      # With password
      curl "https://your-domain/shizuha-drive/api/shares/public/Kj8mN2pQ/?password=secret123"

   **Response:**

   .. code-block:: json

      {
        "type": "file",
        "file": {
          "id": "550e8400-e29b-41d4-a716-446655440001",
          "name": "report.pdf",
          "size": 1048576,
          "mime_type": "application/pdf"
        },
        "permission": "VIEW",
        "download_url": "/api/shares/public/Kj8mN2pQ/download/"
      }

Download Public Share
---------------------

.. http:get:: /api/shares/public/(string:token)/download/

   Download file from a public share.

   :param token: Public share token
   :query password: Password if share is protected

Permission Levels
-----------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Permission
     - Description
   * - ``VIEW``
     - Can view and download files, but cannot modify
   * - ``EDIT``
     - Can view, download, and modify files (rename, move within shared folder)

Share Object Schema
-------------------

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Type
     - Description
   * - id
     - UUID
     - Unique share identifier
   * - file
     - UUID/null
     - Shared file ID
   * - folder
     - UUID/null
     - Shared folder ID
   * - shared_by
     - integer
     - User ID who created the share
   * - shared_with
     - integer/null
     - User ID share is with (user shares)
   * - shared_with_email
     - string/null
     - Email of shared user
   * - permission
     - string
     - ``VIEW`` or ``EDIT``
   * - is_public
     - boolean
     - Whether this is a public link
   * - public_token
     - string/null
     - Token for public access
   * - public_url
     - string/null
     - Full URL for public access
   * - has_password
     - boolean
     - Whether share is password protected
   * - expires_at
     - datetime/null
     - Expiration timestamp (null = never)
   * - access_count
     - integer
     - Number of times share has been accessed
   * - created_at
     - datetime
     - Creation timestamp
