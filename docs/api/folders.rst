Folders API
===========

The Folders API allows you to create and manage folder hierarchies in Shizuha Drive.

List Folders
------------

.. http:get:: /api/folders/

   List all folders owned by the authenticated user.

   :query parent: Filter by parent folder ID. Use ``null`` for root-level folders.
   :query is_starred: Filter starred folders
   :query is_trashed: Filter trashed folders
   :query search: Search by folder name

   **Example Request:**

   .. code-block:: bash

      curl -X GET "https://your-domain/shizuha-drive/api/folders/?parent=null" \
        -H "Authorization: Bearer YOUR_TOKEN"

   **Example Response:**

   .. code-block:: json

      {
        "count": 3,
        "results": [
          {
            "id": "550e8400-e29b-41d4-a716-446655440100",
            "name": "Documents",
            "parent": null,
            "is_starred": false,
            "is_trashed": false,
            "file_count": 15,
            "folder_count": 3,
            "created_at": "2026-01-10T08:00:00Z",
            "updated_at": "2026-01-30T14:30:00Z"
          },
          {
            "id": "550e8400-e29b-41d4-a716-446655440101",
            "name": "Photos",
            "parent": null,
            "is_starred": true,
            "is_trashed": false,
            "file_count": 250,
            "folder_count": 12,
            "created_at": "2026-01-05T10:00:00Z",
            "updated_at": "2026-01-31T09:15:00Z"
          }
        ]
      }

Create Folder
-------------

.. http:post:: /api/folders/

   Create a new folder.

   :<json string name: Folder name (required)
   :<json string parent: Parent folder ID (null for root)

   :statuscode 201: Folder created

   **Example Request:**

   .. code-block:: bash

      curl -X POST "https://your-domain/shizuha-drive/api/folders/" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"name": "Project Files", "parent": null}'

   **Example Response:**

   .. code-block:: json

      {
        "id": "550e8400-e29b-41d4-a716-446655440102",
        "name": "Project Files",
        "parent": null,
        "is_starred": false,
        "is_trashed": false,
        "file_count": 0,
        "folder_count": 0,
        "created_at": "2026-01-31T12:00:00Z",
        "updated_at": "2026-01-31T12:00:00Z"
      }

Get Folder Details
------------------

.. http:get:: /api/folders/(uuid:id)/

   Get details of a specific folder.

   :param id: Folder UUID

   **Example Response:**

   .. code-block:: json

      {
        "id": "550e8400-e29b-41d4-a716-446655440100",
        "name": "Documents",
        "parent": null,
        "path": "/Documents",
        "breadcrumbs": [
          {"id": null, "name": "My Drive"},
          {"id": "550e8400-e29b-41d4-a716-446655440100", "name": "Documents"}
        ],
        "is_starred": false,
        "is_trashed": false,
        "file_count": 15,
        "folder_count": 3,
        "total_size": 52428800,
        "created_at": "2026-01-10T08:00:00Z",
        "updated_at": "2026-01-30T14:30:00Z"
      }

Get Folder Contents
-------------------

.. http:get:: /api/folders/(uuid:id)/contents/

   Get all files and subfolders in a folder.

   :param id: Folder UUID (use ``root`` for root folder)
   :query ordering: Sort by field

   **Example Request:**

   .. code-block:: bash

      curl -X GET "https://your-domain/shizuha-drive/api/folders/550e8400.../contents/" \
        -H "Authorization: Bearer YOUR_TOKEN"

   **Example Response:**

   .. code-block:: json

      {
        "folder": {
          "id": "550e8400-e29b-41d4-a716-446655440100",
          "name": "Documents",
          "path": "/Documents"
        },
        "folders": [
          {
            "id": "550e8400-e29b-41d4-a716-446655440110",
            "name": "Work",
            "file_count": 5
          }
        ],
        "files": [
          {
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "name": "report.pdf",
            "size": 1048576,
            "mime_type": "application/pdf"
          }
        ]
      }

Update Folder
-------------

.. http:patch:: /api/folders/(uuid:id)/

   Update folder metadata (rename, star, etc.).

   :param id: Folder UUID
   :<json string name: New folder name
   :<json boolean is_starred: Star/unstar the folder

   **Example Request:**

   .. code-block:: bash

      curl -X PATCH "https://your-domain/shizuha-drive/api/folders/550e8400.../" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"name": "Important Documents"}'

Delete Folder (Move to Trash)
-----------------------------

.. http:delete:: /api/folders/(uuid:id)/

   Move folder and all its contents to trash.

   :param id: Folder UUID
   :statuscode 204: Folder moved to trash

   .. warning::

      Deleting a folder moves all files and subfolders within it to trash.

Move Folder
-----------

.. http:post:: /api/folders/(uuid:id)/move/

   Move folder to a different parent folder.

   :param id: Folder UUID
   :<json string parent: Target parent folder ID (null for root)

   .. note::

      A folder cannot be moved into itself or its descendants.

   **Example Request:**

   .. code-block:: bash

      curl -X POST "https://your-domain/shizuha-drive/api/folders/550e8400.../move/" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"parent": "550e8400-e29b-41d4-a716-446655440200"}'

Folder Object Schema
--------------------

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Type
     - Description
   * - id
     - UUID
     - Unique folder identifier
   * - name
     - string
     - Folder name
   * - parent
     - UUID/null
     - Parent folder ID (null = root)
   * - path
     - string
     - Full path from root (e.g., ``/Documents/Work``)
   * - is_starred
     - boolean
     - Whether folder is starred
   * - is_trashed
     - boolean
     - Whether folder is in trash
   * - file_count
     - integer
     - Number of files directly in folder
   * - folder_count
     - integer
     - Number of subfolders directly in folder
   * - total_size
     - integer
     - Total size of all contents in bytes
   * - created_at
     - datetime
     - Creation timestamp
   * - updated_at
     - datetime
     - Last modification timestamp
