Quota API
=========

The Quota API allows you to check storage usage and limits.

Get Storage Quota
-----------------

.. http:get:: /api/quota/

   Get the authenticated user's storage quota and usage.

   **Example Request:**

   .. code-block:: bash

      curl -X GET "https://your-domain/shizuha-drive/api/quota/" \
        -H "Authorization: Bearer YOUR_TOKEN"

   **Example Response:**

   .. code-block:: json

      {
        "owner_id": "3",
        "quota_bytes": 107374182400,
        "quota_display": "100.0 GB",
        "used_bytes": 5368709120,
        "used_display": "5.0 GB",
        "remaining_bytes": 102005473280,
        "remaining_display": "95.0 GB",
        "usage_percent": 5.0,
        "file_count": 150,
        "folder_count": 25,
        "is_over_quota": false,
        "warning_threshold": 90,
        "is_warning": false
      }

Response Fields
---------------

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Field
     - Type
     - Description
   * - owner_id
     - string
     - User ID from Shizuha ID
   * - quota_bytes
     - integer
     - Total storage quota in bytes
   * - quota_display
     - string
     - Human-readable quota (e.g., "100.0 GB")
   * - used_bytes
     - integer
     - Currently used storage in bytes
   * - used_display
     - string
     - Human-readable usage
   * - remaining_bytes
     - integer
     - Available storage in bytes
   * - remaining_display
     - string
     - Human-readable remaining
   * - usage_percent
     - float
     - Percentage of quota used (0-100)
   * - file_count
     - integer
     - Total number of files
   * - folder_count
     - integer
     - Total number of folders
   * - is_over_quota
     - boolean
     - True if usage exceeds quota
   * - warning_threshold
     - integer
     - Percentage at which warning is shown
   * - is_warning
     - boolean
     - True if usage exceeds warning threshold

Default Quotas
--------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - User Type
     - Default Quota
   * - Standard User
     - 5 GB
   * - Premium User
     - 100 GB
   * - Enterprise User
     - Unlimited (admin-configured)

Quota Enforcement
-----------------

When a user exceeds their quota:

1. **Upload blocked** - New file uploads return HTTP 507 (Insufficient Storage)
2. **Existing files preserved** - No files are automatically deleted
3. **Warning at 90%** - Users see a warning when approaching quota

Error Response
^^^^^^^^^^^^^^

When attempting to upload over quota:

.. code-block:: json

   {
     "detail": "Storage quota exceeded. You have used 5.0 GB of 5.0 GB.",
     "code": "quota_exceeded",
     "quota_bytes": 5368709120,
     "used_bytes": 5368709120,
     "file_size": 1048576
   }

Trash and Quota
---------------

* Files in trash **still count** toward quota
* Empty trash to reclaim space
* Files auto-delete from trash after 30 days

.. http:get:: /api/trash/

   List items in trash.

.. http:post:: /api/trash/empty/

   Permanently delete all items in trash.

   .. warning::

      This action cannot be undone.

.. http:post:: /api/trash/restore/

   Restore items from trash.

   :<json array items: List of file/folder IDs to restore

   **Example:**

   .. code-block:: bash

      curl -X POST "https://your-domain/shizuha-drive/api/trash/restore/" \
        -H "Authorization: Bearer YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"items": ["550e8400-e29b-41d4-a716-446655440001"]}'

Admin: Set User Quota
---------------------

.. note::

   This endpoint requires admin privileges.

.. http:patch:: /api/admin/quota/(int:user_id)/

   Set storage quota for a user.

   :param user_id: User ID from Shizuha ID
   :<json integer quota_bytes: New quota in bytes

   **Example:**

   .. code-block:: bash

      # Set quota to 100 GB
      curl -X PATCH "https://your-domain/shizuha-drive/api/admin/quota/3/" \
        -H "Authorization: Bearer ADMIN_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"quota_bytes": 107374182400}'

Alternatively, use the CLI script:

.. code-block:: bash

   # Set quota using email
   ./scripts/drive-set-quota.sh user@example.com 100  # 100 GB

   # List all quotas
   ./scripts/drive-list-quotas.sh
