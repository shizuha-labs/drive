"""Regression tests for binary Drive MCP downloads."""

from __future__ import annotations

import base64
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from mcp.types import BlobResourceContents, EmbeddedResource


MCP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_DIR))
os.environ.setdefault("SHIZUHA_DRIVE_URL", "https://drive.example.test")

drive_server = importlib.import_module("drive_server")
base_module = importlib.import_module("base")


def test_api_binary_request_preserves_bytes_and_integrity_headers():
    client = base_module.ShizuhaMCPBase(
        "drive",
        8018,
        api_url="https://drive.example.test",
        jwt_token="test-token",
    )
    response = Mock()
    response.headers = {
        "Content-Length": "4",
        "Content-Type": "application/zip",
        "X-Drive-Integrity": "verified",
        "X-Drive-Checksum-SHA256": "abcd",
    }
    response.status_code = 200
    response.iter_content.return_value = [b"PK", b"12"]
    response.raise_for_status.return_value = None

    with patch.object(base_module.requests, "request", return_value=response):
        result = client.api_binary_request("GET", "/files/example/download/")

    assert result["content"] == b"PK12"
    assert result["headers"]["X-Drive-Integrity"] == "verified"
    assert result["headers"]["X-Drive-Checksum-SHA256"] == "abcd"
    response.close.assert_called_once()


def test_api_binary_request_rejects_declared_oversize_before_streaming():
    client = base_module.ShizuhaMCPBase(
        "drive",
        8018,
        api_url="https://drive.example.test",
        jwt_token="test-token",
    )
    response = Mock()
    response.headers = {"Content-Length": "17"}
    response.status_code = 200
    response.raise_for_status.return_value = None

    with patch.object(base_module.requests, "request", return_value=response):
        result = client.api_binary_request(
            "GET",
            "/files/example/download/",
            max_bytes=16,
        )

    assert result["error"] == "file_too_large_for_mcp"
    assert result["status_code"] == 413
    response.iter_content.assert_not_called()
    response.close.assert_called_once()


def test_download_file_returns_embedded_verified_binary():
    payload = b"PK\x03\x04report"
    with patch.object(
        drive_server.base,
        "api_binary_request",
        return_value={
            "content": payload,
            "status_code": 200,
            "headers": {
                "Content-Type": "application/zip",
                "X-Drive-Integrity": "verified",
                "X-Drive-Checksum-SHA256": "expected-sha",
            },
        },
    ):
        result = drive_server.download_file("file-id")

    assert result.is_error is False
    assert result.structured_content["returned_bytes"] == len(payload)
    assert result.structured_content["integrity"] == "verified"
    embedded = next(
        item for item in result.content if isinstance(item, EmbeddedResource)
    )
    assert isinstance(embedded.resource, BlobResourceContents)
    assert base64.b64decode(embedded.resource.blob) == payload
    assert embedded.resource.mime_type == "application/zip"


def test_download_file_sends_bounded_range():
    with patch.object(
        drive_server.base,
        "api_binary_request",
        return_value={
            "content": b"four",
            "status_code": 206,
            "headers": {
                "Content-Type": "application/octet-stream",
                "Content-Range": "bytes 10-13/100",
                "X-Drive-Integrity": "partial-range",
            },
        },
    ) as request:
        result = drive_server.download_file(
            "file-id",
            offset_bytes=10,
            length_bytes=4,
        )

    assert result.is_error is False
    assert result.structured_content["content_range"] == "bytes 10-13/100"
    request.assert_called_once_with(
        "GET",
        "/files/file-id/download/",
        extra_headers={"Range": "bytes=10-13"},
        max_bytes=16 * 1024 * 1024,
    )
