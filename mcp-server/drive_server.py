#!/usr/bin/env python3
"""
Shizuha Drive MCP Server.

Cloud file storage tools for AI agents.

Tools (30):
- drive_get_quota: Get storage quota and usage
- drive_get_dashboard: Get dashboard overview
- drive_get_activity: Get recent activity log
- drive_list_folders: List folders with optional parent filter
- drive_get_folder: Get folder details
- drive_create_folder: Create a new folder
- drive_update_folder: Update folder name/color/parent
- drive_delete_folder: Delete (trash) a folder
- drive_restore_folder: Restore a folder from trash
- drive_move_folder: Move a folder to a different parent
- drive_get_folder_tree: Get full folder tree hierarchy
- drive_list_files: List files with filtering
- drive_get_file: Get detailed file metadata
- drive_upload_file: Upload a file from base64 content
- drive_update_file: Update file metadata
- drive_delete_file: Delete (trash) a file
- drive_download_file: Download a bounded file or file range
- drive_restore_file: Restore a file from trash
- drive_move_file: Move a file to a different folder
- drive_copy_file: Copy a file
- drive_star_file: Toggle star status on a file
- drive_get_file_versions: Get version history of a file
- drive_get_recent_files: Get recently accessed files
- drive_get_starred_files: Get all starred files
- drive_list_shares: List active shares
- drive_create_share: Create a file/folder link, user, or organization share
- drive_get_share: Get share details
- drive_delete_share: Delete a share
- drive_revoke_share: Revoke an active share
- drive_list_trash: List trashed files and folders
- drive_empty_trash: Permanently delete all trashed items
"""

import argparse
import base64
import json
import os
import sys
from typing import Optional

# Make mcp-server/ importable BEFORE any `from base import ...`: this module is
# also loaded via importlib (spec_from_file_location) by drive tests from an
# arbitrary CWD, where the script-dir auto-insert does not happen.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from base import ShizuhaMCPServer
from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, TextContent
from base import ShizuhaMCPBase, run_mcp_server


def parse_args():
    """Parse command-line arguments for CLI argument compatibility."""
    parser = argparse.ArgumentParser(description="Shizuha Drive MCP Server")
    parser.add_argument(
        "--jwt-token",
        help="JWT token for authentication (overrides SHIZUHA_DRIVE_JWT_TOKEN env var)",
    )
    parser.add_argument(
        "--api-url",
        help="Drive API URL (overrides SHIZUHA_DRIVE_URL env var)",
    )
    args, _ = parser.parse_known_args()
    return args


# Parse CLI args (for callers that don't pass env vars to MCP subprocesses)
_args = parse_args()

# Configuration priority: CLI args > environment > defaults
DRIVE_API_URL = (
    _args.api_url
    or os.environ.get("SHIZUHA_DRIVE_URL")
    or os.environ.get("DRIVE_API_URL")
)
DRIVE_JWT_TOKEN = (
    _args.jwt_token
    or os.environ.get("SHIZUHA_DRIVE_JWT_TOKEN")
    or os.environ.get("DRIVE_JWT_TOKEN")
    or ""
)

# stateless_http (PLAT-612 pattern, adopted 2026-07-05): drive runs 2 replicas
# and streamable-HTTP sessions are replica-local — consecutive POSTs that land
# on different replicas hit "Session not found" (constant agent reconnect
# churn). Stateless mode makes every request self-contained so any replica
# serves any request. Env-driven so the chart's mcp.statelessHttp flag governs.
_STATELESS_HTTP = os.environ.get("SHIZUHA_MCP_STATELESS_HTTP", "true").lower() in ("1", "true", "yes")
mcp = ShizuhaMCPServer("shizuha-drive", stateless_http=_STATELESS_HTTP)

# Advertise OAuth 2.0 discovery metadata so MCP clients can discover the auth server.
# INC-2026-0905 (PLAT-7652): use the shared setup_oauth_routes seam instead of
# discovery-only registration. With SHIZUHA_MCP_OAUTH_ENFORCE=true (set in the
# shizuha-drive chart values) it enables transport-level bearer enforcement —
# FastMCP RequireAuthMiddleware returns 401 + WWW-Authenticate on unauthenticated
# initialize/tools/call, matching the six correct MCP backends. Without the flag
# it registers discovery routes only (previous behavior).
from base import setup_oauth_routes as _setup_oauth_routes
_setup_oauth_routes(mcp, "drive")
base = ShizuhaMCPBase("drive", 8018, api_url=DRIVE_API_URL, jwt_token=DRIVE_JWT_TOKEN)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_bytes(b):
    """Format byte count into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def _file_icon(f):
    """Return an icon string for a file based on its type and starred status."""
    icon = "📄"
    ft = f.get('file_type', '')
    if ft == 'image':
        icon = "🖼️"
    elif ft == 'video':
        icon = "🎬"
    elif ft == 'audio':
        icon = "🎵"
    elif ft == 'archive':
        icon = "📦"
    if f.get('is_starred'):
        icon = "⭐" + icon
    return icon


def _format_file_summary(f):
    """Format a single file as a markdown list item."""
    icon = _file_icon(f)
    out = f"- {icon} **{f.get('name')}**\n"
    out += f"  ID: {f.get('id')}\n"
    out += f"  Size: {_format_bytes(f.get('size', 0))}\n"
    out += f"  Type: {f.get('mime_type', 'unknown')}\n"
    out += f"  Modified: {f.get('updated_at')}\n"
    return out


def _extract_list(result):
    """Extract list items from a paginated or plain list API response."""
    return (result if isinstance(result, list) else result.get("results", []))


# ---------------------------------------------------------------------------
# Quota & Dashboard & Activity
# ---------------------------------------------------------------------------

@mcp.tool(name="drive_get_quota")
def get_quota() -> str:
    """
    Get current storage quota and usage.

    Returns used space, total quota, percentage used, and remaining space.
    """
    result = base.api_request("GET", "/quota/")
    if "error" in result:
        return base.format_error(result)

    used = result.get('used_bytes', 0)
    total = result.get('quota_bytes', 0)
    pct = result.get('usage_percent', 0)

    output = f"# Storage Quota\n\n"
    output += f"**Used**: {_format_bytes(used)}\n"
    output += f"**Total**: {_format_bytes(total)}\n"
    output += f"**Usage**: {pct:.1f}%\n"
    output += f"**Remaining**: {_format_bytes(total - used)}\n"

    return output


@mcp.tool(name="drive_get_dashboard")
def get_dashboard() -> str:
    """
    Get the drive dashboard overview.

    Returns summary statistics: file counts by type, storage usage,
    recent uploads, and sharing activity.
    """
    result = base.api_request("GET", "/dashboard/")
    if "error" in result:
        return base.format_error(result)

    output = "# Drive Dashboard\n\n"

    # Storage
    if 'storage' in result or 'used_bytes' in result:
        storage = result.get('storage', result)
        output += "## Storage\n"
        output += f"**Used**: {_format_bytes(storage.get('used_bytes', 0))}\n"
        output += f"**Total**: {_format_bytes(storage.get('quota_bytes', 0))}\n"
        output += f"**Usage**: {storage.get('usage_percent', 0):.1f}%\n\n"

    # Counts
    if 'counts' in result:
        counts = result['counts']
        output += "## Counts\n"
        for key, val in counts.items():
            output += f"**{key.replace('_', ' ').title()}**: {val}\n"
        output += "\n"

    # File type breakdown
    if 'file_types' in result:
        output += "## Files by Type\n"
        for ft in result['file_types']:
            if isinstance(ft, dict):
                output += f"- **{ft.get('file_type', 'unknown')}**: {ft.get('count', 0)} files ({_format_bytes(ft.get('total_size', 0))})\n"
            else:
                output += f"- {ft}\n"
        output += "\n"

    # Recent uploads
    if 'recent_uploads' in result:
        output += "## Recent Uploads\n"
        for f in result['recent_uploads'][:5]:
            output += f"- **{f.get('name')}** ({_format_bytes(f.get('size', 0))}) - {f.get('created_at')}\n"
        output += "\n"

    # Fallback: dump remaining top-level keys
    shown_keys = {'storage', 'used_bytes', 'quota_bytes', 'usage_percent', 'counts', 'file_types', 'recent_uploads'}
    for key, val in result.items():
        if key not in shown_keys:
            if isinstance(val, (str, int, float, bool)):
                output += f"**{key.replace('_', ' ').title()}**: {val}\n"

    return output


@mcp.tool(name="drive_get_activity")
def get_activity(limit: int = 20) -> str:
    """
    Get recent activity log for the drive.

    Shows file uploads, downloads, shares, moves, deletions, and other events.

    Args:
        limit: Maximum number of activity entries to return
    """
    result = base.api_request("GET", "/activity/", params={"page_size": limit})
    if "error" in result:
        return base.format_error(result)

    items = _extract_list(result)
    if not items:
        return "No recent activity."

    output = f"# Recent Activity ({len(items)} events)\n\n"
    for item in items:
        action = item.get('action', item.get('event_type', 'unknown'))
        target = item.get('file_name', item.get('target', ''))
        user = item.get('user_email', item.get('user', ''))
        timestamp = item.get('created_at', item.get('timestamp', ''))
        output += f"- **{action}** {target}\n"
        if user:
            output += f"  By: {user}\n"
        output += f"  At: {timestamp}\n"

    return output


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

@mcp.tool(name="drive_list_folders")
def list_folders(parent_id: Optional[str] = None) -> str:
    """
    List folders.

    Args:
        parent_id: Parent folder ID to list children of (None for root folders)
    """
    if parent_id:
        result = base.api_request("GET", "/folders/", params={"parent": parent_id})
    else:
        result = base.api_request("GET", "/folders/root/")

    if "error" in result:
        return base.format_error(result)

    folders = _extract_list(result)
    if not folders:
        return "No folders found."

    output = f"# Folders ({len(folders)})\n\n"
    for folder in folders:
        icon = "⭐📁" if folder.get('is_starred') else "📁"
        output += f"- {icon} **{folder.get('name')}**\n"
        output += f"  ID: {folder.get('id')}\n"
        if folder.get('color'):
            output += f"  Color: {folder['color']}\n"
        if folder.get('file_count') is not None:
            output += f"  Files: {folder['file_count']}\n"

    return output


@mcp.tool(name="drive_get_folder")
def get_folder(folder_id: str) -> str:
    """
    Get detailed information about a folder.

    Args:
        folder_id: Folder ID (UUID)
    """
    result = base.api_request("GET", f"/folders/{folder_id}/")
    if "error" in result:
        return base.format_error(result)

    f = result
    output = f"# 📁 {f.get('name')}\n\n"
    output += f"**ID**: {f.get('id')}\n"
    if f.get('parent'):
        output += f"**Parent ID**: {f.get('parent')}\n"
        output += f"**Parent Name**: {f.get('parent_name', 'N/A')}\n"
    else:
        output += f"**Parent**: Root\n"
    if f.get('color'):
        output += f"**Color**: {f['color']}\n"
    output += f"**Starred**: {f.get('is_starred', False)}\n"
    if f.get('file_count') is not None:
        output += f"**Files**: {f['file_count']}\n"
    if f.get('total_size') is not None:
        output += f"**Total Size**: {_format_bytes(f['total_size'])}\n"
    output += f"\n**Created**: {f.get('created_at')}\n"
    output += f"**Modified**: {f.get('updated_at')}\n"

    return output


@mcp.tool(name="drive_create_folder")
def create_folder(name: str, parent_id: Optional[str] = None, color: Optional[str] = None) -> str:
    """
    Create a new folder.

    Args:
        name: Folder name
        parent_id: Parent folder ID (None for root)
        color: Folder color (hex code, e.g. "#FF5733")
    """
    data = {"name": name}
    if parent_id:
        data["parent"] = parent_id
    if color:
        data["color"] = color

    result = base.api_request("POST", "/folders/", data=data)
    if "error" in result:
        return base.format_error(result)

    folder = result
    return (
        f"Folder created!\n\n"
        f"**Name**: {folder.get('name')}\n"
        f"**ID**: {folder.get('id')}\n"
    )


@mcp.tool(name="drive_update_folder")
def update_folder(
    folder_id: str,
    name: Optional[str] = None,
    color: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> str:
    """
    Update a folder's properties.

    Args:
        folder_id: Folder ID to update
        name: New folder name
        color: New folder color (hex code)
        parent_id: New parent folder ID (moves the folder)
    """
    data = {}
    if name:
        data["name"] = name
    if color:
        data["color"] = color
    if parent_id is not None:
        data["parent"] = parent_id

    if not data:
        return "Error: No fields provided to update."

    result = base.api_request("PATCH", f"/folders/{folder_id}/", data=data)
    if "error" in result:
        return base.format_error(result)

    folder = result
    return (
        f"Folder updated!\n\n"
        f"**Name**: {folder.get('name')}\n"
        f"**ID**: {folder.get('id')}\n"
    )


@mcp.tool(name="drive_delete_folder")
def delete_folder(folder_id: str) -> str:
    """
    Delete (trash) a folder and its contents.

    Args:
        folder_id: Folder ID to delete
    """
    result = base.api_request("DELETE", f"/folders/{folder_id}/")
    if "error" in result:
        return base.format_error(result)

    return "Folder moved to trash."


@mcp.tool(name="drive_restore_folder")
def restore_folder(folder_id: str) -> str:
    """
    Restore a folder from trash.

    Args:
        folder_id: Folder ID to restore
    """
    result = base.api_request("POST", f"/folders/{folder_id}/restore/")
    if "error" in result:
        return base.format_error(result)

    return "Folder restored from trash."


@mcp.tool(name="drive_move_folder")
def move_folder(folder_id: str, parent_id: Optional[str] = None) -> str:
    """
    Move a folder to a different parent folder.

    Args:
        folder_id: Folder ID to move
        parent_id: Target parent folder ID (None for root)
    """
    data = {"parent": parent_id}
    result = base.api_request("POST", f"/folders/{folder_id}/move/", data=data)
    if "error" in result:
        return base.format_error(result)

    return "Folder moved successfully."


@mcp.tool(name="drive_get_folder_tree")
def get_folder_tree() -> str:
    """
    Get the full folder tree hierarchy.

    Returns all folders in a nested tree structure showing the complete
    folder organization.
    """
    result = base.api_request("GET", "/folders/tree/")
    if "error" in result:
        return base.format_error(result)

    folders = _extract_list(result)
    if not folders:
        return "No folders found."

    output = "# Folder Tree\n\n"

    def format_folder(folder, indent=0):
        prefix = "  " * indent
        icon = "⭐📁" if folder.get('is_starred') else "📁"
        out = f"{prefix}- {icon} **{folder.get('name')}**\n"
        out += f"{prefix}  ID: {folder.get('id')}\n"
        if folder.get('children'):
            for child in folder['children']:
                out += format_folder(child, indent + 1)
        return out

    for folder in folders:
        output += format_folder(folder)

    return output


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

@mcp.tool(name="drive_list_files")
def list_files(
    folder_id: Optional[str] = None,
    file_type: Optional[str] = None,
    is_starred: Optional[bool] = None,
    search: Optional[str] = None,
    ordering: Optional[str] = None,
    limit: int = 20,
) -> str:
    """
    List files with filtering.

    Args:
        folder_id: Filter by folder ID
        file_type: Filter by type (document, image, video, audio, archive, other)
        is_starred: Filter starred files only
        search: Search by filename
        ordering: Sort order (name, -name, size, -size, updated_at, -updated_at)
        limit: Maximum files to return
    """
    params = {"page_size": limit}
    if folder_id:
        params["folder"] = folder_id
    if file_type:
        params["file_type"] = file_type
    if is_starred is not None:
        params["is_starred"] = is_starred
    if search:
        params["search"] = search
    if ordering:
        params["ordering"] = ordering

    result = base.api_request("GET", "/files/", params=params)
    if "error" in result:
        return base.format_error(result)

    files = _extract_list(result)
    if not files:
        return "No files found."

    output = f"# Files ({len(files)})\n\n"
    for f in files:
        output += _format_file_summary(f)

    return output


@mcp.tool(name="drive_get_file")
def get_file(file_id: str) -> str:
    """
    Get detailed file metadata.

    Args:
        file_id: File ID (UUID)
    """
    result = base.api_request("GET", f"/files/{file_id}/")
    if "error" in result:
        return base.format_error(result)

    f = result

    output = f"# {f.get('name')}\n\n"
    output += f"**ID**: {f.get('id')}\n"
    output += f"**Type**: {f.get('file_type')}\n"
    output += f"**MIME Type**: {f.get('mime_type')}\n"
    output += f"**Size**: {_format_bytes(f.get('size', 0))}\n"
    output += f"**Extension**: {f.get('extension', 'N/A')}\n"

    if f.get('width') and f.get('height'):
        output += f"**Dimensions**: {f['width']}x{f['height']}\n"
    if f.get('duration'):
        output += f"**Duration**: {f['duration']}s\n"

    output += f"\n**Folder**: {f.get('folder_name', f.get('folder', 'Root'))}\n"
    output += f"**Starred**: {f.get('is_starred', False)}\n"
    output += f"**Version**: {f.get('version', 1)}\n"

    output += f"\n**Created**: {f.get('created_at')}\n"
    output += f"**Modified**: {f.get('updated_at')}\n"

    if f.get('uploaded_by_name') or f.get('uploaded_by'):
        output += f"**Uploaded By**: {f.get('uploaded_by_name', f.get('uploaded_by'))}\n"

    if f.get('md5_checksum'):
        output += f"\n**MD5**: {f['md5_checksum']}\n"
    if f.get('sha256_checksum'):
        output += f"**SHA256**: {f['sha256_checksum']}\n"

    return output


@mcp.tool(name="drive_upload_file")
def upload_file(
    filename: str,
    content_base64: str,
    folder_id: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    Upload a file from base64-encoded content.

    Args:
        filename: Name for the uploaded file (e.g. "report.pdf")
        content_base64: Base64-encoded file content
        folder_id: Folder ID to upload into (None for root)
        description: Optional file description
    """
    try:
        file_bytes = base64.b64decode(content_base64)
    except Exception as e:
        return f"Error: Invalid base64 content: {e}"

    import io
    files = {"file": (filename, io.BytesIO(file_bytes))}
    data = {}
    if folder_id:
        data["folder"] = folder_id
    if description:
        data["description"] = description

    result = base.api_request("POST", "/files/", data=data, files=files, timeout=60)
    if "error" in result:
        return base.format_error(result)

    f = result
    return (
        f"File uploaded!\n\n"
        f"**Name**: {f.get('name')}\n"
        f"**ID**: {f.get('id')}\n"
        f"**Size**: {_format_bytes(f.get('size', 0))}\n"
        f"**Type**: {f.get('mime_type', 'unknown')}\n"
    )


@mcp.tool(name="drive_update_file")
def update_file(
    file_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> str:
    """
    Update file metadata.

    Args:
        file_id: File ID to update
        name: New filename
        description: New description
        folder_id: Move to a different folder
    """
    data = {}
    if name:
        data["name"] = name
    if description is not None:
        data["description"] = description
    if folder_id is not None:
        data["folder"] = folder_id

    if not data:
        return "Error: No fields provided to update."

    # DRV optimistic concurrency requires the current version for
    # clobber-capable metadata writes. Resolve it immediately before PATCH so
    # an MCP caller does not need to know the REST-only base_version field.
    if "name" in data or "folder" in data:
        current = base.api_request("GET", f"/files/{file_id}/")
        if "error" in current:
            return base.format_error(current)
        base_version = current.get("version")
        if base_version is None:
            return "Error: Drive did not return the file version required for update."
        data["base_version"] = base_version

    result = base.api_request("PATCH", f"/files/{file_id}/", data=data)
    if "error" in result:
        return base.format_error(result)

    f = result
    return (
        f"File updated!\n\n"
        f"**Name**: {f.get('name')}\n"
        f"**ID**: {f.get('id')}\n"
    )


@mcp.tool(name="drive_delete_file")
def delete_file(file_id: str) -> str:
    """
    Delete (trash) a file.

    The file is moved to trash and can be restored later.

    Args:
        file_id: File ID to delete
    """
    result = base.api_request("DELETE", f"/files/{file_id}/")
    if "error" in result:
        return base.format_error(result)

    return "File moved to trash."


@mcp.tool(name="drive_download_file", structured_output=False)
def download_file(
    file_id: str,
    offset_bytes: int = 0,
    length_bytes: Optional[int] = None,
) -> CallToolResult:
    """
    Download file content as an embedded binary MCP resource.

    Args:
        file_id: File ID (UUID)
        offset_bytes: Optional zero-based byte offset for a ranged download
        length_bytes: Optional number of bytes to return (maximum 16 MiB)

    Files up to 16 MiB can be returned in one call. Larger files must be read
    in bounded ranges by passing offset_bytes and length_bytes.
    """
    max_bytes = 16 * 1024 * 1024
    if offset_bytes < 0:
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text="offset_bytes must be non-negative.")],
        )
    if length_bytes is not None and not 1 <= length_bytes <= max_bytes:
        return CallToolResult(
            isError=True,
            content=[
                TextContent(
                    type="text",
                    text=f"length_bytes must be between 1 and {max_bytes}.",
                )
            ],
        )

    extra_headers = {}
    if offset_bytes or length_bytes is not None:
        requested_length = length_bytes or max_bytes
        end = offset_bytes + requested_length - 1
        extra_headers["Range"] = f"bytes={offset_bytes}-{end}"

    result = base.api_binary_request(
        "GET",
        f"/files/{file_id}/download/",
        extra_headers=extra_headers,
        max_bytes=max_bytes,
    )
    if "error" in result:
        detail = result.get("detail") or {}
        if result.get("error") == "file_too_large_for_mcp":
            detail_text = json.dumps(detail, sort_keys=True)
            message = (
                "File exceeds the 16 MiB MCP response limit. Retry with "
                f"offset_bytes and length_bytes <= {max_bytes}. Details: {detail_text}"
            )
        else:
            message = base.format_error(result)
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=message)],
        )

    content = result["content"]
    headers = {key.lower(): value for key, value in result["headers"].items()}
    content_range = headers.get("content-range")
    mime_type = headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    uri = f"drive://files/{file_id}"
    if content_range:
        uri += f"?range={content_range.replace(' ', '%20')}"
    metadata = {
        "file_id": file_id,
        "returned_bytes": len(content),
        "content_range": content_range,
        "mime_type": mime_type,
        "integrity": headers.get("x-drive-integrity"),
        "checksum_sha256": headers.get("x-drive-checksum-sha256"),
    }
    summary = "\n".join(
        [
            "File downloaded as an embedded binary resource.",
            f"File ID: {file_id}",
            f"Returned bytes: {len(content)}",
            f"Content type: {mime_type}",
            f"Content range: {content_range or 'full file'}",
            f"Integrity: {metadata['integrity'] or 'not reported'}",
            f"SHA-256: {metadata['checksum_sha256'] or 'not reported'}",
        ]
    )
    return CallToolResult(
        content=[
            TextContent(type="text", text=summary),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri=uri,
                    mimeType=mime_type,
                    blob=base64.b64encode(content).decode("ascii"),
                ),
            ),
        ],
        structuredContent=metadata,
    )


@mcp.tool(name="drive_restore_file")
def restore_file(file_id: str) -> str:
    """
    Restore a file from trash.

    Args:
        file_id: File ID to restore
    """
    result = base.api_request("POST", f"/files/{file_id}/restore/")
    if "error" in result:
        return base.format_error(result)

    return "File restored from trash."


@mcp.tool(name="drive_move_file")
def move_file(file_id: str, folder_id: Optional[str] = None) -> str:
    """
    Move a file to a different folder.

    Args:
        file_id: File ID to move
        folder_id: Target folder ID (None for root)
    """
    data = {"folder": folder_id}
    result = base.api_request("POST", f"/files/{file_id}/move/", data=data)
    if "error" in result:
        return base.format_error(result)

    return "File moved successfully."


@mcp.tool(name="drive_copy_file")
def copy_file(file_id: str, folder_id: Optional[str] = None, new_name: Optional[str] = None) -> str:
    """
    Copy a file.

    Args:
        file_id: File ID to copy
        folder_id: Target folder ID (None for same folder)
        new_name: New filename (optional)
    """
    data = {}
    if folder_id:
        data["folder"] = folder_id
    if new_name:
        data["name"] = new_name

    result = base.api_request("POST", f"/files/{file_id}/copy/", data=data)
    if "error" in result:
        return base.format_error(result)

    copy = result
    return (
        f"File copied!\n\n"
        f"**New File ID**: {copy.get('id')}\n"
        f"**Name**: {copy.get('name')}\n"
    )


@mcp.tool(name="drive_star_file")
def star_file(file_id: str) -> str:
    """
    Toggle star status on a file.

    Args:
        file_id: File ID
    """
    result = base.api_request("POST", f"/files/{file_id}/star/")
    if "error" in result:
        return base.format_error(result)

    is_starred = result.get('is_starred', False)
    return f"File {'starred' if is_starred else 'unstarred'}."


@mcp.tool(name="drive_get_file_versions")
def get_file_versions(file_id: str) -> str:
    """
    Get version history of a file.

    Shows all previous versions with upload timestamps and sizes.

    Args:
        file_id: File ID (UUID)
    """
    result = base.api_request("GET", f"/files/{file_id}/versions/")
    if "error" in result:
        return base.format_error(result)

    versions = _extract_list(result)
    if not versions:
        return "No version history available."

    output = f"# Version History ({len(versions)} versions)\n\n"
    for v in versions:
        version_num = v.get('version', v.get('version_number', ''))
        output += f"## Version {version_num}\n"
        output += f"**ID**: {v.get('id')}\n"
        if v.get('size') is not None:
            output += f"**Size**: {_format_bytes(v['size'])}\n"
        if v.get('uploaded_by_name') or v.get('uploaded_by'):
            output += f"**Uploaded By**: {v.get('uploaded_by_name', v.get('uploaded_by'))}\n"
        output += f"**Created**: {v.get('created_at')}\n"
        if v.get('md5_checksum'):
            output += f"**MD5**: {v['md5_checksum']}\n"
        output += "\n"

    return output


@mcp.tool(name="drive_get_recent_files")
def get_recent_files(limit: int = 10) -> str:
    """
    Get recently accessed files.

    Args:
        limit: Maximum files to return
    """
    result = base.api_request("GET", "/files/recent/", params={"limit": limit})
    if "error" in result:
        return base.format_error(result)

    files = _extract_list(result)
    if not files:
        return "No recent files."

    output = f"# Recent Files ({len(files)})\n\n"
    for f in files:
        output += f"- **{f.get('name')}**\n"
        output += f"  ID: {f.get('id')}\n"
        output += f"  Size: {_format_bytes(f.get('size', 0))}\n"
        output += f"  Accessed: {f.get('last_accessed_at', f.get('updated_at'))}\n"

    return output


@mcp.tool(name="drive_get_starred_files")
def get_starred_files(limit: int = 50) -> str:
    """
    Get all starred files.

    Args:
        limit: Maximum files to return
    """
    result = base.api_request("GET", "/files/starred/", params={"page_size": limit})
    if "error" in result:
        return base.format_error(result)

    files = _extract_list(result)
    if not files:
        return "No starred files."

    output = f"# Starred Files ({len(files)})\n\n"
    for f in files:
        output += _format_file_summary(f)

    return output


# ---------------------------------------------------------------------------
# Shares
# ---------------------------------------------------------------------------

@mcp.tool(name="drive_list_shares")
def list_shares(file_id: Optional[str] = None, limit: int = 20) -> str:
    """
    List active shares.

    Args:
        file_id: Filter shares for a specific file
        limit: Maximum shares to return
    """
    params = {"page_size": limit}
    if file_id:
        params["file"] = file_id

    result = base.api_request("GET", "/shares/", params=params)
    if "error" in result:
        return base.format_error(result)

    shares = _extract_list(result)
    if not shares:
        return "No shares found."

    output = f"# Shares ({len(shares)})\n\n"
    for s in shares:
        share_type = s.get('share_type', 'link')
        icon = "🔗" if share_type == 'link' else "👤"
        output += f"- {icon} **{s.get('file_name', s.get('file', 'Unknown'))}**\n"
        output += f"  Share ID: {s.get('id')}\n"
        output += f"  Type: {share_type}\n"
        output += f"  Permission: {s.get('permission', 'view')}\n"
        if s.get('shared_with_email'):
            output += f"  Shared With: {s['shared_with_email']}\n"
        if s.get('token'):
            output += f"  Token: {s['token']}\n"
        if s.get('expires_at'):
            output += f"  Expires: {s['expires_at']}\n"
        output += f"  Created: {s.get('created_at')}\n"

    return output


@mcp.tool(name="drive_create_share")
def create_share(
    file_id: Optional[str] = None,
    folder_id: Optional[str] = None,
    permission: str = "view",
    email: Optional[str] = None,
    organization_id: Optional[int] = None,
    expires_in_days: Optional[int] = None,
    password: Optional[str] = None,
    allow_download: bool = True,
    as_user: bool = False,
) -> str:
    """
    Create a share for one file or folder.

    Creates a public link, user-specific share, or organization share. An
    organization share is always scoped by an explicit organization membership.

    Args:
        file_id: File ID to share (exactly one of file_id/folder_id)
        folder_id: Folder ID to share (exactly one of file_id/folder_id)
        permission: Permission level (view, comment, edit)
        email: Share with specific user email (mutually exclusive with organization_id)
        organization_id: Share with this exact organization
        expires_in_days: Days until share expires
        password: Password-protect the share link
        allow_download: Allow downloading the file via the share
        as_user: Use the delegated user's identity instead of the agent identity
    """
    if bool(file_id) == bool(folder_id):
        return "Error: specify exactly one of file_id or folder_id."
    if email and organization_id is not None:
        return "Error: email and organization_id are mutually exclusive."
    if organization_id is not None and int(organization_id) <= 0:
        return "Error: organization_id must be a positive integer."

    data = {
        "permission": permission,
        "allow_download": allow_download,
    }
    data["file" if file_id else "folder"] = file_id or folder_id

    if organization_id is not None:
        data["share_type"] = "org"
        data["grantee_org_id"] = str(organization_id)
    elif email:
        data["share_type"] = "user"
        data["shared_with_email"] = email
    else:
        data["share_type"] = "link"

    if expires_in_days:
        from datetime import datetime, timedelta
        data["expires_at"] = (datetime.now() + timedelta(days=expires_in_days)).isoformat()

    if password:
        data["password"] = password

    try:
        result = base.api_request(
            "POST",
            "/shares/",
            data=data,
            as_user=as_user,
            organization_id=organization_id,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    if "error" in result:
        return base.format_error(result)

    share = result
    output = "Share created!\n\n"
    output += f"**Share ID**: {share.get('id')}\n"
    output += f"**Target**: {share.get('target_type')} {share.get('target_name')}\n"
    output += f"**Type**: {share.get('share_type')}\n"
    output += f"**Permission**: {share.get('permission')}\n"
    if share.get('organization_id'):
        output += f"**Organization ID**: {share.get('organization_id')}\n"
    token = share.get('link_token') or share.get('token')
    if token:
        output += f"**Public Link Token**: {token}\n"
    public = share.get('share_url') or (
        f"https://shizuha.com/drive/share/{token}" if token else None
    )
    if public:
        output += f"**Public URL**: {public}\n"
    if share.get('shared_with_email'):
        output += f"**Shared With**: {share.get('shared_with_email')}\n"
    if share.get('expires_at'):
        output += f"**Expires**: {share.get('expires_at')}\n"

    return output


@mcp.tool(name="drive_get_share")
def get_share(share_id: str) -> str:
    """
    Get details of a specific share.

    Args:
        share_id: Share ID (UUID)
    """
    result = base.api_request("GET", f"/shares/{share_id}/")
    if "error" in result:
        return base.format_error(result)

    s = result
    output = f"# Share Details\n\n"
    output += f"**Share ID**: {s.get('id')}\n"
    output += f"**File**: {s.get('file_name', s.get('file'))}\n"
    output += f"**Type**: {s.get('share_type', 'link')}\n"
    output += f"**Permission**: {s.get('permission', 'view')}\n"
    output += f"**Allow Download**: {s.get('allow_download', True)}\n"
    if s.get('shared_with_email'):
        output += f"**Shared With**: {s['shared_with_email']}\n"
    token = s.get('link_token') or s.get('token')
    if token:
        output += f"**Token**: {token}\n"
    public = s.get('share_url') or (
        f"https://shizuha.com/drive/share/{token}" if token else None
    )
    if public:
        output += f"**Public URL**: {public}\n"
    if s.get('password'):
        output += f"**Password Protected**: Yes\n"
    if s.get('expires_at'):
        output += f"**Expires**: {s['expires_at']}\n"
    output += f"**Created**: {s.get('created_at')}\n"
    if s.get('access_count') is not None:
        output += f"**Access Count**: {s['access_count']}\n"

    return output


@mcp.tool(name="drive_delete_share")
def delete_share(share_id: str) -> str:
    """
    Delete a share permanently.

    Args:
        share_id: Share ID to delete
    """
    result = base.api_request("DELETE", f"/shares/{share_id}/")
    if "error" in result:
        return base.format_error(result)

    return "Share deleted."


@mcp.tool(name="drive_revoke_share")
def revoke_share(share_id: str) -> str:
    """
    Revoke an active share (disables access without deleting the record).

    Args:
        share_id: Share ID to revoke
    """
    result = base.api_request("POST", f"/shares/{share_id}/revoke/")
    if "error" in result:
        return base.format_error(result)

    return "Share revoked."


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------

@mcp.tool(name="drive_list_trash")
def list_trash() -> str:
    """
    List all trashed files and folders.
    """
    result = base.api_request("GET", "/trash/")
    if "error" in result:
        return base.format_error(result)

    items = _extract_list(result)
    if not items:
        return "Trash is empty."

    output = f"# Trash ({len(items)} items)\n\n"
    for item in items:
        item_type = "📁" if item.get('is_folder') else "📄"
        output += f"- {item_type} **{item.get('name')}**\n"
        output += f"  ID: {item.get('id')}\n"
        if item.get('size') is not None and not item.get('is_folder'):
            output += f"  Size: {_format_bytes(item['size'])}\n"
        output += f"  Trashed: {item.get('trashed_at', item.get('updated_at'))}\n"

    return output


@mcp.tool(name="drive_empty_trash")
def empty_trash() -> str:
    """
    Permanently delete all items in trash.

    WARNING: This action cannot be undone. All trashed files and folders
    will be permanently deleted and storage space will be freed.
    """
    result = base.api_request("POST", "/trash/empty/")
    if "error" in result:
        return base.format_error(result)

    return "Trash emptied. All trashed items have been permanently deleted."


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server."""
    run_mcp_server(mcp, base)


if __name__ == "__main__":
    main()
