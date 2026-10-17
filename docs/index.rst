Shizuha Drive Documentation
============================

**Shizuha Drive** is a cloud file storage service similar to Google Drive, providing secure file upload, download, sharing, and organization capabilities as part of the Shizuha Platform.

.. image:: _static/architecture-overview.png
   :alt: Shizuha Drive Architecture
   :align: center

Features
--------

* **File Management** - Upload, download, rename, move, and delete files
* **Folder Organization** - Hierarchical folder structure with unlimited nesting
* **File Sharing** - Share files/folders with users or via public links
* **Storage Quotas** - Per-user storage limits with usage tracking
* **File Versioning** - Track and restore previous file versions
* **Thumbnails** - Automatic thumbnail generation for images
* **Trash & Recovery** - Soft delete with 30-day recovery window
* **S3 Storage Backend** - MinIO-powered object storage for scalability

Quick Links
-----------

* :doc:`quickstart` - Get started in 5 minutes
* :doc:`api/index` - REST API reference
* :doc:`architecture/index` - System architecture
* :doc:`admin/index` - Administration guide

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   quickstart
   installation
   configuration

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/index
   api/authentication
   api/files
   api/folders
   api/shares
   api/quota

.. toctree::
   :maxdepth: 2
   :caption: Architecture

   architecture/index
   architecture/storage
   architecture/data-model
   architecture/async-tasks

.. toctree::
   :maxdepth: 2
   :caption: Administration

   admin/index
   admin/deployment
   admin/minio
   admin/quotas
   admin/troubleshooting

.. toctree::
   :maxdepth: 1
   :caption: Development

   development/contributing
   development/testing


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
