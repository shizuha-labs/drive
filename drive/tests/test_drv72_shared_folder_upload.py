"""DRV-72: organization-edit folders are valid upload destinations."""
import importlib.util
import os
from types import SimpleNamespace
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import (
    File,
    FileVersion,
    Folder,
    ResumableUpload,
    Share,
    SharePermission,
    ShareType,
)
from drive.serializers import (
    FileUploadSerializer,
    FolderNotWritable,
    ResumableUploadCreateSerializer,
)
from drive.views import FileViewSet, FolderViewSet, ResumableUploadViewSet


def _request(user):
    return SimpleNamespace(user=user)


def _load_drive_server():
    module_path = Path(__file__).resolve().parents[2] / "mcp-server" / "drive_server.py"
    spec = importlib.util.spec_from_file_location("drive_mcp_server_drv72", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    os.environ.setdefault("SHIZUHA_DRIVE_URL", "http://drive.test/api")
    os.environ.setdefault("SHIZUHA_DRIVE_JWT_TOKEN", "test-token")
    spec.loader.exec_module(module)
    return module


class _FakeBase:
    def __init__(self):
        self.calls = []

    def api_request(
        self,
        method,
        path,
        *,
        data=None,
        as_user=False,
        organization_id=None,
    ):
        self.calls.append((method, path, data, as_user, organization_id))
        return {
            "id": "share-1",
            "folder": data.get("folder"),
            "target_type": "folder",
            "target_name": "FY2025-26",
            "share_type": data["share_type"],
            "permission": data["permission"],
            "organization_id": str(organization_id),
        }

    def format_error(self, result):
        return f"error: {result}"


class CreateOrganizationShareToolTests(SimpleTestCase):
    def test_folder_org_share_is_user_delegated_and_tenant_scoped(self):
        server = _load_drive_server()
        fake = _FakeBase()
        server.base = fake

        result = server.create_share(
            folder_id="11111111-1111-1111-1111-111111111111",
            permission="edit",
            organization_id=2,
            as_user=True,
        )

        self.assertIn("Share created!", result)
        self.assertEqual(
            fake.calls,
            [
                (
                    "POST",
                    "/shares/",
                    {
                        "folder": "11111111-1111-1111-1111-111111111111",
                        "permission": "edit",
                        "allow_download": True,
                        "share_type": "org",
                        "grantee_org_id": "2",
                    },
                    True,
                    2,
                )
            ],
        )

    def test_share_tool_rejects_ambiguous_target(self):
        server = _load_drive_server()
        server.base = _FakeBase()

        result = server.create_share(
            file_id="11111111-1111-1111-1111-111111111111",
            folder_id="22222222-2222-2222-2222-222222222222",
        )

        self.assertIn("exactly one", result)
        self.assertEqual(server.base.calls, [])


class SharedFolderUploadTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("folder-owner")
        self.member = User.objects.create_user("org-member")
        self.member.organization_id = "2"
        self.other_org = User.objects.create_user("other-org-member")
        self.other_org.organization_id = "4"
        self.folder = Folder.objects.create(
            owner_id=str(self.owner.id),
            name="FY2025-26",
        )

    def _share(self, permission):
        return Share.objects.create(
            folder=self.folder,
            share_type=ShareType.ORGANIZATION,
            permission=permission,
            organization_id="2",
            created_by_id=str(self.owner.id),
        )

    def test_direct_upload_accepts_edit_shared_folder(self):
        self._share(SharePermission.EDIT)
        serializer = FileUploadSerializer(context={"request": _request(self.member)})

        self.assertEqual(serializer.validate_folder(self.folder.id), self.folder.id)

    def test_direct_upload_accepts_one_matching_membership_for_multi_org_user(self):
        self._share(SharePermission.EDIT)
        self.member.organization_id = None
        self.member.organization_ids = ["2", "203"]
        self.member.org_ids = self.member.organization_ids
        serializer = FileUploadSerializer(context={"request": _request(self.member)})

        self.assertEqual(serializer.validate_folder(self.folder.id), self.folder.id)

    def test_direct_upload_hides_view_only_and_wrong_org_folders(self):
        self._share(SharePermission.VIEW)

        for user in (self.member, self.other_org):
            serializer = FileUploadSerializer(context={"request": _request(user)})
            with self.assertRaises(FolderNotWritable) as raised:
                serializer.validate_folder(self.folder.id)
            self.assertEqual(
                raised.exception.get_codes(),
                ["folder_not_found_or_not_editable"],
            )

    def test_direct_upload_preserves_safe_folder_error_code(self):
        self._share(SharePermission.VIEW)
        request = APIRequestFactory().post(
            "/api/files/",
            {
                "folder": str(self.folder.id),
                "file": SimpleUploadedFile(
                    "report.txt",
                    b"not written",
                    content_type="text/plain",
                ),
            },
            format="multipart",
        )
        force_authenticate(request, user=self.member)

        response = FileViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"],
            "folder_not_found_or_not_editable",
        )
        self.assertFalse(File.objects.filter(name="report.txt").exists())

    def test_folder_capabilities_distinguish_view_from_edit_without_mutation(self):
        share = self._share(SharePermission.VIEW)
        view = FolderViewSet.as_view({"get": "capabilities"})

        request = APIRequestFactory().get(
            f"/api/folders/{self.folder.id}/capabilities/"
        )
        force_authenticate(request, user=self.member)
        response = view(request, pk=self.folder.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "folder_id": str(self.folder.id),
                "can_view": True,
                "can_edit": False,
            },
        )

        share.permission = SharePermission.EDIT
        share.save(update_fields=["permission"])
        request = APIRequestFactory().get(
            f"/api/folders/{self.folder.id}/capabilities/"
        )
        force_authenticate(request, user=self.member)
        response = view(request, pk=self.folder.id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["can_edit"])

    def test_resumable_upload_accepts_edit_shared_folder(self):
        self._share(SharePermission.EDIT)
        serializer = ResumableUploadCreateSerializer(
            data={
                "filename": "report.json",
                "total_size": 128,
                "folder_id": str(self.folder.id),
            },
            context={"request": _request(self.member)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["folder"], self.folder.id)

    def test_resumable_upload_rejects_wrong_org(self):
        self._share(SharePermission.EDIT)
        serializer = ResumableUploadCreateSerializer(
            data={
                "filename": "report.json",
                "total_size": 128,
                "folder_id": str(self.folder.id),
            },
            context={"request": _request(self.other_org)},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("folder", str(serializer.errors).lower())

    def test_resumable_upload_endpoint_returns_safe_error_code_for_view_only(self):
        """P2 boundary regression: the create endpoint returns HTTP 400 with the
        exact stable code and creates no upload session for a VIEW-only caller."""
        self._share(SharePermission.VIEW)
        view = ResumableUploadViewSet.as_view({"post": "create"})
        request = APIRequestFactory().post(
            "/api/uploads/resumable/",
            {
                "filename": "report.json",
                "total_size": 128,
                "folder": str(self.folder.id),
            },
            format="json",
        )
        force_authenticate(request, user=self.member)

        self.assertEqual(ResumableUpload.objects.count(), 0)
        response = view(request)

        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.data.get("error"), dict)
        self.assertEqual(
            response.data["error"]["code"],
            "folder_not_found_or_not_editable",
        )
        self.assertEqual(ResumableUpload.objects.count(), 0)

    def test_resumable_upload_endpoint_no_session_for_wrong_org(self):
        """P2 boundary regression: wrong-org caller gets HTTP 400, the stable
        code, and no upload session is persisted."""
        self._share(SharePermission.EDIT)
        view = ResumableUploadViewSet.as_view({"post": "create"})
        request = APIRequestFactory().post(
            "/api/uploads/resumable/",
            {
                "filename": "report.json",
                "total_size": 128,
                "folder": str(self.folder.id),
            },
            format="json",
        )
        force_authenticate(request, user=self.other_org)

        self.assertEqual(ResumableUpload.objects.count(), 0)
        response = view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"]["code"],
            "folder_not_found_or_not_editable",
        )
        self.assertEqual(ResumableUpload.objects.count(), 0)

    def test_capabilities_404_keeps_hidden_id_for_no_view(self):
        """P2 boundary regression: a caller with no VIEW access receives 404
        with no capability payload, preserving hidden-ID non-disclosure."""
        # No org share exists for this folder on any tenant.
        view = FolderViewSet.as_view({"get": "capabilities"})

        for user in (self.member, self.other_org):
            request = APIRequestFactory().get(
                f"/api/folders/{self.folder.id}/capabilities/"
            )
            force_authenticate(request, user=user)
            response = view(request, pk=self.folder.id)

            self.assertEqual(response.status_code, 404)
            self.assertNotIn("can_view", response.data)
            self.assertNotIn("can_edit", response.data)
            self.assertNotIn("folder_id", response.data)

    def test_version_history_honors_inherited_org_folder_share(self):
        self._share(SharePermission.EDIT)
        file_obj = File.objects.create(
            owner_id=str(self.owner.id),
            folder=self.folder,
            name="report.zip",
            mime_type="application/zip",
            size=10,
        )
        version = FileVersion.objects.create(
            file=file_obj,
            version_number=1,
            file_data=ContentFile(b"old-report", name="old-report.zip"),
            size=10,
            created_by_id=str(self.owner.id),
        )
        request = APIRequestFactory().get(f"/api/files/{file_obj.id}/versions/")
        force_authenticate(request, user=self.member)

        response = FileViewSet.as_view({"get": "versions"})(
            request,
            pk=str(file_obj.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [str(row["id"]) for row in response.data],
            [str(version.id)],
        )

    def test_version_history_hides_inherited_folder_from_other_org(self):
        self._share(SharePermission.EDIT)
        file_obj = File.objects.create(
            owner_id=str(self.owner.id),
            folder=self.folder,
            name="report.zip",
            mime_type="application/zip",
            size=10,
        )
        request = APIRequestFactory().get(f"/api/files/{file_obj.id}/versions/")
        force_authenticate(request, user=self.other_org)

        response = FileViewSet.as_view({"get": "versions"})(
            request,
            pk=str(file_obj.id),
        )

        self.assertEqual(response.status_code, 404)
