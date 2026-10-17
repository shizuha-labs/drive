"""
Django Admin configuration for Shizuha Drive
"""
from django.contrib import admin
from .models import Folder, File, FileVersion, Thumbnail, Share, StorageQuota, Activity


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner_id', 'parent', 'is_starred', 'is_trashed', 'created_at']
    list_filter = ['is_starred', 'is_trashed', 'created_at']
    search_fields = ['name', 'owner_id']
    raw_id_fields = ['parent']


@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner_id', 'folder', 'file_type', 'size', 'is_starred', 'is_trashed', 'created_at']
    list_filter = ['file_type', 'is_starred', 'is_trashed', 'is_processed', 'created_at']
    search_fields = ['name', 'owner_id', 'mime_type']
    raw_id_fields = ['folder']
    readonly_fields = ['checksum_md5', 'checksum_sha256', 'is_processed']


@admin.register(FileVersion)
class FileVersionAdmin(admin.ModelAdmin):
    list_display = ['file', 'version_number', 'size', 'created_by_id', 'created_at']
    list_filter = ['created_at']
    search_fields = ['file__name']
    raw_id_fields = ['file']


@admin.register(Thumbnail)
class ThumbnailAdmin(admin.ModelAdmin):
    list_display = ['file', 'size', 'width', 'height', 'created_at']
    list_filter = ['size', 'created_at']
    raw_id_fields = ['file']


@admin.register(Share)
class ShareAdmin(admin.ModelAdmin):
    list_display = ['id', 'share_type', 'permission', 'created_by_id', 'is_active', 'expires_at', 'access_count']
    list_filter = ['share_type', 'permission', 'is_active', 'created_at']
    search_fields = ['link_token', 'shared_with_email']
    raw_id_fields = ['file', 'folder']


@admin.register(StorageQuota)
class StorageQuotaAdmin(admin.ModelAdmin):
    list_display = ['owner_id', 'used_human', 'quota_human', 'usage_percent', 'file_count', 'folder_count']
    search_fields = ['owner_id']
    readonly_fields = ['used_bytes', 'file_count', 'folder_count']


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ['action', 'user_id', 'file', 'folder', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['user_id']
    raw_id_fields = ['file', 'folder']
