import importlib.util
import os
from pathlib import Path

os.environ.setdefault("SHIZUHA_DRIVE_URL", "http://drive.test")
os.environ.setdefault("SHIZUHA_DRIVE_JWT_TOKEN", "test-token")


def load_drive_server():
    module_path = Path(__file__).with_name("drive_server.py")
    spec = importlib.util.spec_from_file_location(
        "drive_mcp_server_for_metadata_tests",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeBase:
    def __init__(self):
        self.calls = []

    def api_request(self, method, path, params=None, data=None, **kwargs):
        self.calls.append((method, path, data))
        if method == "GET":
            return {"id": "file-1", "version": 7}
        if method == "PATCH":
            return {"id": "file-1", "name": data.get("name", "report.zip")}
        if method == "POST" and path.endswith("/move/"):
            return {"id": "file-1"}
        raise AssertionError((method, path, data))

    @staticmethod
    def format_error(result):
        return f"ERR {result}"


def test_update_file_resolves_current_version_before_json_patch():
    server = load_drive_server()
    fake = FakeBase()
    server.base = fake

    result = server.update_file(
        "file-1",
        name="renamed-report.zip",
        folder_id="folder-2",
    )

    assert "File updated!" in result
    assert fake.calls == [
        ("GET", "/files/file-1/", None),
        (
            "PATCH",
            "/files/file-1/",
            {
                "name": "renamed-report.zip",
                "folder": "folder-2",
                "base_version": 7,
            },
        ),
    ]


def test_move_file_uses_structured_json_action():
    server = load_drive_server()
    fake = FakeBase()
    server.base = fake

    result = server.move_file("file-1", folder_id="folder-2")

    assert result == "File moved successfully."
    assert fake.calls == [
        ("POST", "/files/file-1/move/", {"folder": "folder-2"}),
    ]
