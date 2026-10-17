# CLAUDE.md - Shizuha Drive

## Overview

**Shizuha Drive** is a cloud file storage service (Google Drive-like) with folder hierarchy, file sharing, and user quotas.

| Setting | Value |
|---------|-------|
| Backend Port | 8018 |
| Frontend Port | 5182 |
| Database | shizuha_drive |
| Redis DB | 9 |
| URL Path | `/drive/` |
| Storage | MinIO on S1 |

## Access

| URL | Description |
|-----|-------------|
| `/drive/` | File Storage UI |
| `/drive/api/` | REST API |
| `/drive/admin/` | Django Admin |

## API Endpoints

### Files
```
GET/POST      /drive/api/files/                   # List/upload files
GET/PATCH/DEL /drive/api/files/{id}/              # File operations
GET           /drive/api/files/{id}/download/     # Download file
POST          /drive/api/files/{id}/share/        # Create share link
```

### Folders
```
GET/POST      /drive/api/folders/                 # List/create folders
GET/PATCH/DEL /drive/api/folders/{id}/            # Folder operations
```

### Sharing & Quota
```
GET           /drive/api/shared/                  # Files shared with user
GET           /drive/api/recent/                  # Recently accessed files
GET           /drive/api/quota/                   # Current quota usage
```

## Key Models

- **File** - File metadata (name, size, mime type, storage path)
- **Folder** - Folder hierarchy
- **Share** - File/folder sharing with permissions
- **Quota** - User storage quota tracking

## Features

- Drag-and-drop file upload
- Folder hierarchy navigation
- File sharing with link or user permissions
- User storage quotas (default: 5GB)
- File preview for common types
- MinIO S3-compatible backend storage

## Commands

All development commands run from the `compose/` directory using the master docker-compose.yaml:

### Development (Docker Compose)
```bash
# Start service with frontend
cd compose && docker compose up -d shizuha-drive shizuha-drive-frontend

# Run migrations
cd compose && docker compose exec shizuha-drive python manage.py migrate

# Run tests
cd compose && docker compose exec shizuha-drive python manage.py test

# View logs
cd compose && docker compose logs -f shizuha-drive
```

### Quota Management
```bash
# Set user quota to 100GB
./scripts/drive-set-quota.sh user@example.com 100

# List all user quotas
./scripts/drive-list-quotas.sh
```

## Project Structure

```
shizuha-drive/
├── drivemanager/           # Django project settings
├── drive/                  # Main Django app
│   ├── models.py           # File, Folder, Share, Quota
│   ├── views.py            # DRF ViewSets
│   ├── serializers.py      # DRF serializers
│   ├── tasks.py            # Celery tasks (cleanup, thumbnails)
│   └── storage.py          # MinIO integration
└── frontend/               # React + Vite app
```

Also reference [../CLAUDE.md](../CLAUDE.md) for platform-wide rules.
