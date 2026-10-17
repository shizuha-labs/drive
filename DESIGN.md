# Shizuha Drive - Design Document

Deployment examples below use generic storage hosts and a service-owned data
directory. Supply your reviewed endpoint and credentials through managed
configuration; no private host layout or common password is required.

## Overview

Shizuha Drive is a cloud file storage service similar to Google Drive, providing file upload, download, sharing, and organization capabilities.

## Architecture

### Storage Backend

Drive uses **MinIO** (S3-compatible object storage) for file storage:

```
┌─────────────────────────────────────────────────────────────────┐
│   control-plane (K3s Control Plane)     S1 (K3s Worker)             │
│   ┌───────────────────┐             ┌───────────────────┐       │
│   │  shizuha-drive    │   HTTP/S3   │     MinIO         │       │
│   │  Django + Celery  │────────────>│   NodePort 30900  │       │
│   │  Port 8018        │             │                   │       │
│   └───────────────────┘             │   Data Path:      │       │
│                                     │   /srv/shizuha-drive/  │       │
│   ┌───────────────────┐             │   minio-data/     │       │
│   │  drive-frontend   │             │   (~500GB avail)  │       │
│   │  Vite + React     │             └───────────────────┘       │
│   │  Port 5182        │                                         │
│   └───────────────────┘                                         │
└─────────────────────────────────────────────────────────────────┘
```

### Why MinIO?

1. **S3-compatible API** - Industry standard, easy migration to cloud
2. **django-storages support** - Minimal code changes (declared in pyproject.toml / uv.lock)
3. **HTTP-based** - Works across nodes without kernel modules
4. **Built-in features** - Versioning, lifecycle policies, web console
5. **Leverages S1's storage** - 500GB+ available on physical server

### Cross-Node Networking

If your network requires an external storage endpoint, configure Drive explicitly; for example:
- **External endpoint**: `http://storage.example.invalid:30900` (S1's LAN IP + NodePort)
- **Not** the internal ClusterIP service

## Data Model

### Core Models

```
File
├── id (UUID)
├── name
├── folder (FK to Folder)
├── owner_id (from Shizuha ID)
├── file (FileField → MinIO)
├── size, mime_type
├── md5_checksum, sha256_checksum
├── is_starred, is_trashed
├── created_at, updated_at

Folder
├── id (UUID)
├── name
├── parent (self-referential FK)
├── owner_id
├── is_starred, is_trashed

Share
├── file/folder (FK)
├── shared_by, shared_with
├── permission (VIEW/EDIT)
├── public_token (for link sharing)
├── password_hash, expires_at

StorageQuota
├── owner_id (unique)
├── quota_bytes (default: 5GB)
├── used_bytes
├── file_count
```

### File Storage Path

Files are stored in MinIO with the path pattern:
```
files/{owner_id}/{year}/{month}/{uuid}_{filename}
```

Example: `files/3/2026/01/a1b2c3d4_report.pdf`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/files/` | GET | List files in folder |
| `/api/files/` | POST | Upload file |
| `/api/files/{id}/` | GET/PATCH/DELETE | File operations |
| `/api/files/{id}/download/` | GET | Download file |
| `/api/files/{id}/move/` | POST | Move file |
| `/api/files/{id}/copy/` | POST | Copy file |
| `/api/folders/` | GET/POST | List/create folders |
| `/api/folders/{id}/` | GET/PATCH/DELETE | Folder operations |
| `/api/shares/` | GET/POST | Manage shares |
| `/api/shares/public/{token}/` | GET | Public share access |
| `/api/quota/` | GET | Get storage quota |
| `/api/trash/` | GET | List trashed items |
| `/api/trash/restore/` | POST | Restore from trash |

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `USE_S3_STORAGE` | Enable MinIO storage | `0` |
| `AWS_S3_ENDPOINT_URL` | MinIO endpoint | - |
| `AWS_ACCESS_KEY_ID` | MinIO user | - |
| `AWS_SECRET_ACCESS_KEY` | MinIO password | - |
| `AWS_STORAGE_BUCKET_NAME` | Bucket name | `shizuha-drive` |

### Django Settings

When `USE_S3_STORAGE=1`:
```python
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    },
}
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_S3_ADDRESSING_STYLE = 'path'  # Required for MinIO
```

## Quota Management

User quotas are tracked in PostgreSQL (not MinIO):

```python
class StorageQuota(models.Model):
    owner_id = models.CharField(unique=True)
    quota_bytes = models.BigIntegerField(default=5368709120)  # 5GB
    used_bytes = models.BigIntegerField(default=0)
```

### Admin Scripts

```bash
# Set user quota (requires email lookup via Shizuha ID)
./scripts/drive-set-quota.sh user@example.com 100  # 100GB

# List all quotas
./scripts/drive-list-quotas.sh
```

## Async Tasks (Celery)

| Task | Trigger | Description |
|------|---------|-------------|
| `process_file_upload` | After upload | Extract metadata, generate thumbnails |
| `generate_thumbnails` | File processing | Create small/medium/large thumbnails |
| `cleanup_trash` | Scheduled | Delete files in trash > 30 days |
| `cleanup_orphaned_files` | Scheduled | Remove DB orphans from storage |
| `recalculate_quotas` | Scheduled | Sync quota usage with actual storage |

## Frontend

- **Framework**: React 18 + Vite 5
- **State**: TanStack Query for server state
- **Styling**: Tailwind CSS
- **Features**: Grid/list view, drag-drop upload, breadcrumb navigation

## Deployment

### Helm Values (values-local.yaml)

```yaml
shizuhaDrive:
  enabled: true
  image:
    repository: docker.io/library/shizuha-drive
    tag: latest

minio:
  enabled: true
  nodeHostname: "storage-node"
  dataPath: "/srv/shizuha-drive/minio"
  externalEndpoint: "http://storage.example.invalid:30900"
  bucketName: "shizuha-drive"
```

### Helm Upgrade

```bash
cd deploy/k3s/charts/shizuha-stack
helm upgrade shizuha . -n shizuha -f values-local.yaml
```

## Troubleshooting

### MinIO Connection Issues

1. Check MinIO is running:
   ```bash
   kubectl -n shizuha get pod -l app.kubernetes.io/component=minio
   ```

2. Test connectivity from Drive pod:
   ```bash
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python -c "import socket; socket.create_connection(('storage.example.invalid', 30900), timeout=5)"
   ```

3. Verify bucket exists:
   ```bash
   curl http://storage.example.invalid:30900/minio/health/live
   ```

### Storage Issues

1. Check quota:
   ```bash
   ./scripts/drive-list-quotas.sh
   ```

2. Check MinIO storage on S1:
   ```bash
   ssh storage-node "du -sh /srv/shizuha-drive/minio/"
   ```
