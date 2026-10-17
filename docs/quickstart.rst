Quickstart Guide
================

This guide will help you get started with Shizuha Drive in 5 minutes.

Prerequisites
-------------

* Shizuha ID account (for authentication)
* Access to the Shizuha Platform

Accessing Drive
---------------

1. Navigate to ``https://your-domain/drive/`` in your browser
2. Log in with your Shizuha ID credentials
3. You'll be redirected to your personal Drive space

Uploading Files
---------------

**Via Web Interface:**

1. Click the **Upload** button or drag files into the browser
2. Select files from your computer
3. Wait for the upload progress to complete

**Via API:**

.. code-block:: bash

   curl -X POST https://your-domain/shizuha-drive/api/files/ \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -F "file=@/path/to/your/file.pdf" \
     -F "folder=FOLDER_ID"  # Optional

Creating Folders
----------------

.. code-block:: bash

   curl -X POST https://your-domain/shizuha-drive/api/folders/ \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "My Documents", "parent": null}'

Sharing Files
-------------

**Create a public share link:**

.. code-block:: bash

   curl -X POST https://your-domain/shizuha-drive/api/shares/ \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file": "FILE_ID",
       "is_public": true,
       "permission": "VIEW"
     }'

**Share with specific user:**

.. code-block:: bash

   curl -X POST https://your-domain/shizuha-drive/api/shares/ \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file": "FILE_ID",
       "shared_with": "USER_ID",
       "permission": "EDIT"
     }'

Checking Storage Quota
----------------------

.. code-block:: bash

   curl https://your-domain/shizuha-drive/api/quota/ \
     -H "Authorization: Bearer YOUR_JWT_TOKEN"

Response:

.. code-block:: json

   {
     "quota_bytes": 107374182400,
     "used_bytes": 52428800,
     "remaining_bytes": 107321753600,
     "file_count": 15,
     "usage_percent": 0.05
   }

Next Steps
----------

* Read the :doc:`api/index` for complete API documentation
* Learn about :doc:`architecture/storage` for understanding storage backends
* See :doc:`admin/quotas` for managing user quotas
