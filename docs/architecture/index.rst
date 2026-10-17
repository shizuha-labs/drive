Architecture Overview
=====================

Shizuha Drive is built as a microservice within the Shizuha Platform, providing cloud file storage with a Google Drive-like experience.

System Architecture
-------------------

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────────────────┐
   │                           Shizuha Platform                              │
   ├─────────────────────────────────────────────────────────────────────────┤
   │                                                                         │
   │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                 │
   │  │   Nginx     │    │ Shizuha ID  │    │   Redis     │                 │
   │  │   Proxy     │───▶│   (Auth)    │    │  (Celery)   │                 │
   │  └──────┬──────┘    └─────────────┘    └──────┬──────┘                 │
   │         │                                      │                        │
   │         ▼                                      ▼                        │
   │  ┌─────────────────────────────────────────────────────────────────┐   │
   │  │                      Shizuha Drive                              │   │
   │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────────┐   │   │
   │  │  │ Django/DRF    │  │ Celery Worker │  │ Celery Beat       │   │   │
   │  │  │ REST API      │  │ (async tasks) │  │ (scheduled tasks) │   │   │
   │  │  │ Port 8018     │  │               │  │                   │   │   │
   │  │  └───────┬───────┘  └───────────────┘  └───────────────────┘   │   │
   │  │          │                                                      │   │
   │  │          ▼                                                      │   │
   │  │  ┌───────────────────────────────────────────────────────┐     │   │
   │  │  │              Storage Layer (Pluggable)                │     │   │
   │  │  │  ┌─────────────────┐  ┌─────────────────────────┐    │     │   │
   │  │  │  │ PostgreSQL      │  │ MinIO (S3)              │    │     │   │
   │  │  │  │ (metadata)      │  │ (file storage)          │    │     │   │
   │  │  │  │ - Files         │  │ - Actual file content   │    │     │   │
   │  │  │  │ - Folders       │  │ - Thumbnails            │    │     │   │
   │  │  │  │ - Shares        │  │                         │    │     │   │
   │  │  │  │ - Quotas        │  │                         │    │     │   │
   │  │  │  └─────────────────┘  └─────────────────────────┘    │     │   │
   │  │  └───────────────────────────────────────────────────────┘     │   │
   │  └─────────────────────────────────────────────────────────────────┘   │
   │                                                                         │
   │  ┌─────────────────────────────────────────────────────────────────┐   │
   │  │                    Drive Frontend (React)                       │   │
   │  │                         Port 5182                               │   │
   │  └─────────────────────────────────────────────────────────────────┘   │
   │                                                                         │
   └─────────────────────────────────────────────────────────────────────────┘

Component Overview
------------------

**Backend (Django + DRF)**
   REST API server handling all file operations, authentication, and business logic.
   Port 8018.

**Celery Worker**
   Async task processing for file uploads (metadata extraction, thumbnail generation).

**Celery Beat**
   Scheduled tasks (trash cleanup, quota recalculation, orphan file cleanup).

**PostgreSQL**
   Stores all metadata: files, folders, shares, quotas, versions.

**MinIO (S3)**
   Object storage for actual file content. S3-compatible API.

**Frontend (React + Vite)**
   Single-page application with file browser, upload/download, sharing UI.
   Port 5182.

Technology Stack
----------------

.. list-table::
   :header-rows: 1
   :widths: 20 30 50

   * - Layer
     - Technology
     - Purpose
   * - Backend
     - Django 4.2 + DRF
     - REST API, ORM, authentication
   * - Task Queue
     - Celery + Redis
     - Async file processing
   * - Database
     - PostgreSQL 14
     - Metadata storage
   * - Object Storage
     - MinIO
     - S3-compatible file storage
   * - Frontend
     - React 18 + Vite 5
     - SPA with TanStack Query
   * - Styling
     - Tailwind CSS
     - Utility-first CSS

Request Flow
------------

**File Upload:**

1. User uploads file via frontend
2. Django receives multipart form data
3. File is saved to MinIO via django-storages
4. Metadata record created in PostgreSQL
5. Celery task queued for processing
6. Worker extracts metadata, generates thumbnails
7. Response returned to user

**File Download:**

1. User requests file download
2. Django verifies permissions
3. Presigned URL generated for MinIO
4. File streamed to user

**Public Share Access:**

1. User accesses share URL with token
2. Django looks up share record
3. Password verified if protected
4. File metadata/download provided

Security Model
--------------

* **Authentication**: JWT tokens from Shizuha ID
* **Authorization**: Owner-based with explicit shares
* **File Isolation**: Each user's files are isolated by owner_id
* **Public Shares**: Optional password protection, expiration
* **Checksums**: MD5 and SHA-256 for integrity verification

Contents
--------

.. toctree::
   :maxdepth: 2

   storage
   data-model
   async-tasks
