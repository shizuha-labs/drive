# Shizuha Drive

File storage, folders, sharing and quota management for the Shizuha platform.
Use the platform self-host installer with PostgreSQL, Redis and optional S3 storage.

Set `SECRET_KEY` explicitly; startup refuses an empty value. External installations
generate their own key. The first-party CI preserves the effective existing key in
an immutable `drive-runtime-credentials` Secret before requiring its environment
reference. It compares every ready replica twice and refuses changing or inconsistent
sources. The prerequisite manifest contains an empty Secret, never credential bytes.
