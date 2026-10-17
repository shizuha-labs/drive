Files API
=========

The Files API allows you to upload, download, and manage files in Shizuha Drive.

List Files
----------

.. http:get:: /api/files/

   List all files owned by the authenticated user.

   :query folder: Filter by folder ID (UUID). Use ``null`` for root folder.
   :query is_starred: Filter starred files (``true``/``false``)
   :query is_trashed: Filter trashed files (``true``/``false``)
   :query search: Search by filename
   :query ordering: Sort by field (``name``, ``size``, ``created_at``, ``-created_at``)
   :query page: Page number for pagination
   :query page_size: Number of results per page (default: 50, max: 100)

   :>json int count: Total number of results
   :>json string next: URL to next page
   :>json string previous: URL to previous page
   :>json array results: List of file objects

   **Example Request:**

   .. code-block:: bash

      curl -X GET "https://your-domain/shizuha-drive/api/files/?folder=null&ordering=-created_at" \
        -H "Authorization: Bearer YOUR_TOKEN"

   **Example Response:**

   .. code-block:: json

      {
        "count": 2,
        "next": null,
        "previous": null,
        "results": [
          {
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "name": "report.pdf",
            "folder": null,
            "size": 1048576,
            "mime_type": "application/pdf",
            "file_type": "document",
            "is_starred": false,
            "is_trashed": false,
            "thumbnail_url": null,
            "created_at": "2026-01-15T10:30:00Z",
            "updated_at": "2026-01-15T10:30:00Z"
          },
          {
            "id": "550e8400-e29b-41d4-a716-446655440002",
            "name": "photo.jpg",
            "folder": null,
            "size": 2097152,
            "mime_type": "image/jpeg",
            "file_type": "image",
            "is_starred": true,
            "is_trashed": false,
            "thumbnail_url": "/api/files/550e.../thumbnail/",
            "created_at": "2026-01-14T15:20:00Z",
            "updated_at": "2026-01-14T15:20:00Z"
          }
        ]
      }

Upload File
-----------

.. http:post:: /api/files/

   Upload a new file.

   :form file: The file to upload (required)
   :form folder: Folder ID to upload to (optional, null for root)
   :form name: Custom filename (optional, defaults to original name)

   :statuscode 201: File uploaded successfully
   :statuscode 400: Invalid request
   :statuscode 413: File too large
   :statuscode 507: Quota exceeded

   **Example Request:**

   .. code-block:: bash

      curl -X POST "https://your-domain/shizuha-drive/api/files/" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -F "file=@/path/to/document.pdf" \
        -F "folder=550e8400-e29b-41d4-a716-446655440000"

   **Example Response:**

   .. code-block:: json

      {
        "id": "550e8400-e29b-41d4-a716-446655440003",
        "name": "document.pdf",
        "folder": "550e8400-e29b-41d4-a716-446655440000",
        "size": 524288,
        "mime_type": "application/pdf",
        "file_type": "document",
        "md5_checksum": "d41d8cd98f00b204e9800998ecf8427e",
        "sha256_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "is_starred": false,
        "is_trashed": false,
        "created_at": "2026-01-31T12:00:00Z",
        "updated_at": "2026-01-31T12:00:00Z"
      }

Get File Details
----------------

.. http:get:: /api/files/(uuid:id)/

   Get details of a specific file.

   :param id: File UUID

   **Example Response:**

   .. code-block:: json

      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "name": "report.pdf",
        "folder": null,
        "folder_path": "/",
        "size": 1048576,
        "mime_type": "application/pdf",
        "file_type": "document",
        "md5_checksum": "d41d8cd98f00b204e9800998ecf8427e",
        "sha256_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "is_starred": false,
        "is_trashed": false,
        "width": null,
        "height": null,
        "duration": null,
        "created_at": "2026-01-15T10:30:00Z",
        "updated_at": "2026-01-15T10:30:00Z",
        "versions": [
          {
            "id": "550e8400-e29b-41d4-a716-446655440010",
            "version_number": 1,
            "size": 1048576,
            "created_at": "2026-01-15T10:30:00Z"
          }
        ]
      }

Update File
-----------

.. http:patch:: /api/files/(uuid:id)/

   Update file metadata (rename, star, etc.).

   :param id: File UUID
   :<json string name: New filename
   :<json boolean is_starred: Star/unstar the file

   **Example Request:**

   .. code-block:: bash

      curl -X PATCH "https://your-domain/shizuha-drive/api/files/550e8400.../" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"name": "renamed-report.pdf", "is_starred": true}'

Delete File (Move to Trash)
---------------------------

.. http:delete:: /api/files/(uuid:id)/

   Move file to trash. Files in trash are permanently deleted after 30 days.

   :param id: File UUID
   :statuscode 204: File moved to trash

   **Example Request:**

   .. code-block:: bash

      curl -X DELETE "https://your-domain/shizuha-drive/api/files/550e8400.../" \
        -H "Authorization: Bearer YOUR_TOKEN"

Download File
-------------

.. http:get:: /api/files/(uuid:id)/download/

   Download file content.

   :param id: File UUID
   :resheader Content-Disposition: ``attachment; filename="filename.ext"``
   :resheader Content-Type: File MIME type

   **Example Request:**

   .. code-block:: bash

      curl -X GET "https://your-domain/shizuha-drive/api/files/550e8400.../download/" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -o downloaded-file.pdf

Move File
---------

.. http:post:: /api/files/(uuid:id)/move/

   Move file to a different folder.

   :param id: File UUID
   :<json string folder: Target folder ID (use ``null`` for root)

   **Example Request:**

   .. code-block:: bash

      curl -X POST "https://your-domain/shizuha-drive/api/files/550e8400.../move/" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"folder": "550e8400-e29b-41d4-a716-446655440099"}'

Copy File
---------

.. http:post:: /api/files/(uuid:id)/copy/

   Create a copy of the file in a folder.

   :param id: File UUID
   :<json string folder: Target folder ID (use ``null`` for root)
   :<json string name: Name for the copy (optional)

   :statuscode 201: Copy created

   **Example Request:**

   .. code-block:: bash

      curl -X POST "https://your-domain/shizuha-drive/api/files/550e8400.../copy/" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"folder": null, "name": "report-copy.pdf"}'

Get Thumbnail
-------------

.. http:get:: /api/files/(uuid:id)/thumbnail/

   Get thumbnail for image/video files.

   :param id: File UUID
   :query size: Thumbnail size (``small``, ``medium``, ``large``)
   :resheader Content-Type: ``image/jpeg``

   **Example Request:**

   .. code-block:: bash

      curl -X GET "https://your-domain/shizuha-drive/api/files/550e8400.../thumbnail/?size=medium" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -o thumbnail.jpg

File Object Schema
------------------

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Type
     - Description
   * - id
     - UUID
     - Unique file identifier
   * - name
     - string
     - Filename with extension
   * - folder
     - UUID/null
     - Parent folder ID (null = root)
   * - size
     - integer
     - File size in bytes
   * - mime_type
     - string
     - MIME type (e.g., ``application/pdf``)
   * - file_type
     - string
     - Category: ``document``, ``image``, ``video``, ``audio``, ``archive``, ``other``
   * - md5_checksum
     - string
     - MD5 hash of file content
   * - sha256_checksum
     - string
     - SHA-256 hash of file content
   * - is_starred
     - boolean
     - Whether file is starred
   * - is_trashed
     - boolean
     - Whether file is in trash
   * - width
     - integer/null
     - Image/video width in pixels
   * - height
     - integer/null
     - Image/video height in pixels
   * - duration
     - float/null
     - Audio/video duration in seconds
   * - thumbnail_url
     - string/null
     - URL to thumbnail (images/videos only)
   * - created_at
     - datetime
     - Creation timestamp (ISO 8601)
   * - updated_at
     - datetime
     - Last modification timestamp
