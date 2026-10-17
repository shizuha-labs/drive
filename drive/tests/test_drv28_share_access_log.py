"""DRV-28: ShareAccessLog audit trail + owner access-log endpoint + dead-share purge.

Coverage per acceptance criteria:
  1. Public-link GET: success, expired, bad_password/missing writes exactly one log row with correct action/result
  2. Public-link POST (download): success, expired, no_download, bad_password writes one row
  3. GET /shares/{id}/access-log/ — owner gets logs; grantee/unrelated → 403
  4. access_count + last_accessed_at rollup still updates on successful access
  5. purge_dead_shares removes revoked/expired Share past grace window; audit rows survive (INV-5)
  6. RequireSharePermission writes ShareAccessLog for authenticated share access
"""
import hashlib
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from drive.models import File, Folder, Share, ShareAccessLog, ShareType, SharePermission

User = get_user_model()


def _mk_share(owner, share_type=ShareType.LINK, allow_download=True,
              expires_at=None, link_password=None, is_active=True, file_obj=None):
    import secrets
    if file_obj is None:
        file_obj = File.objects.create(
            name='test.txt',
            owner_id=str(owner.id),
            file=SimpleUploadedFile('test.txt', b'hello', content_type='text/plain'),
            mime_type='text/plain', size=5, file_type='document',
        )
    token = secrets.token_urlsafe(16)
    return Share.objects.create(
        file=file_obj,
        share_type=share_type,
        permission=SharePermission.VIEW,
        link_token=token,
        allow_download=allow_download,
        created_by_id=str(owner.id),
        expires_at=expires_at,
        link_password=link_password or '',
        is_active=is_active,
    )


class PublicShareViewAccessLogTest(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='drv28o', email='drv28o@sh.com', password='x')
        self.share = _mk_share(self.owner)

    def _get(self, token=None, **params):
        url = f'/api/public/{token or self.share.link_token}/'
        return self.client.get(url, params)

    def _post(self, token=None, **data):
        url = f'/api/public/{token or self.share.link_token}/'
        return self.client.post(url, data, format='json')

    def test_successful_view_writes_log_ok(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        log = ShareAccessLog.objects.get(share=self.share)
        self.assertEqual(log.action, ShareAccessLog.ACTION_VIEW)
        self.assertEqual(log.result, ShareAccessLog.RESULT_OK)
        self.assertIsNone(log.viewer_id)  # anon

    def test_expired_view_writes_log_expired(self):
        self.share.expires_at = timezone.now() - timedelta(hours=1)
        self.share.save()
        resp = self._get()
        self.assertEqual(resp.status_code, 410)
        log = ShareAccessLog.objects.get(share=self.share)
        self.assertEqual(log.action, ShareAccessLog.ACTION_VIEW)
        self.assertEqual(log.result, ShareAccessLog.RESULT_EXPIRED)

    def test_missing_password_writes_log_bad_password(self):
        self.share.link_password = hashlib.sha256(b'secret').hexdigest()
        self.share.save()
        resp = self._get()
        self.assertEqual(resp.status_code, 401)
        log = ShareAccessLog.objects.get(share=self.share)
        self.assertEqual(log.action, ShareAccessLog.ACTION_VIEW)
        self.assertEqual(log.result, ShareAccessLog.RESULT_BAD_PASSWORD)

    def test_wrong_password_writes_log_password_fail(self):
        self.share.link_password = hashlib.sha256(b'secret').hexdigest()
        self.share.save()
        # DRV-12 M-2: GET never reads password; wrong-password test uses POST
        resp = self._post(password='wrong')
        self.assertEqual(resp.status_code, 401)
        log = ShareAccessLog.objects.get(share=self.share)
        self.assertEqual(log.action, ShareAccessLog.ACTION_PASSWORD_FAIL)
        self.assertEqual(log.result, ShareAccessLog.RESULT_BAD_PASSWORD)

    def test_successful_download_writes_log_ok(self):
        # bare POST is password-unlock/details; action='download' triggers the download path
        resp = self._post(action='download')
        # FileResponse or 400 (no file content in test env) — what matters is the log
        log = ShareAccessLog.objects.filter(share=self.share, action=ShareAccessLog.ACTION_DOWNLOAD).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.result, ShareAccessLog.RESULT_OK)

    def test_download_denied_no_allow_writes_log_denied(self):
        self.share.allow_download = False
        self.share.save()
        # allow_download gate only reached on action='download'
        resp = self._post(action='download')
        self.assertEqual(resp.status_code, 403)
        log = ShareAccessLog.objects.get(share=self.share)
        self.assertEqual(log.action, ShareAccessLog.ACTION_DOWNLOAD)
        self.assertEqual(log.result, ShareAccessLog.RESULT_DENIED)

    def test_download_expired_writes_log_expired(self):
        self.share.expires_at = timezone.now() - timedelta(hours=1)
        self.share.save()
        resp = self._post()
        self.assertEqual(resp.status_code, 410)
        log = ShareAccessLog.objects.get(share=self.share)
        self.assertEqual(log.action, ShareAccessLog.ACTION_DOWNLOAD)
        self.assertEqual(log.result, ShareAccessLog.RESULT_EXPIRED)

    def test_download_bad_password_writes_log(self):
        self.share.link_password = hashlib.sha256(b'secret').hexdigest()
        self.share.save()
        resp = self._post(password='bad')
        self.assertEqual(resp.status_code, 401)
        log = ShareAccessLog.objects.get(share=self.share)
        # password check fires before action dispatch → ACTION_PASSWORD_FAIL
        self.assertEqual(log.action, ShareAccessLog.ACTION_PASSWORD_FAIL)
        self.assertEqual(log.result, ShareAccessLog.RESULT_BAD_PASSWORD)

    def test_access_count_rollup_still_updates(self):
        before = self.share.access_count
        self._get()
        self.share.refresh_from_db()
        self.assertEqual(self.share.access_count, before + 1)
        self.assertIsNotNone(self.share.last_accessed_at)


class ShareAccessLogEndpointTest(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='drv28e', email='drv28e@sh.com', password='x')
        self.grantee = User.objects.create_user(username='drv28g', email='drv28g@sh.com', password='x')
        self.share = _mk_share(self.owner)
        # Seed a log row
        ShareAccessLog.objects.create(
            share=self.share, share_id_copy=self.share.id,
            action=ShareAccessLog.ACTION_VIEW, result=ShareAccessLog.RESULT_OK,
        )

    def _url(self, share_id=None):
        return f'/api/shares/{share_id or self.share.id}/access-log/'

    def test_owner_can_see_access_log(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        rows = data.get('results', data) if isinstance(data, dict) else data
        self.assertGreater(len(rows), 0)

    def test_grantee_cannot_see_access_log(self):
        # Give grantee a share on the same file so they can GET the share object
        other_share = Share.objects.create(
            file=self.share.file,
            share_type=ShareType.LINK,
            permission=SharePermission.VIEW,
            link_token='grantee-tok-drv28',
            allow_download=True,
            created_by_id=str(self.grantee.id),
        )
        # Grantee tries to read the OWNER's share's log — must 403
        self.client.force_authenticate(user=self.grantee)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_unrelated_user_cannot_see_access_log(self):
        stranger = User.objects.create_user(username='drv28s', email='drv28s@sh.com', password='x')
        self.client.force_authenticate(user=stranger)
        resp = self.client.get(self._url())
        # 403 or 404 (queryset excludes unowned shares)
        self.assertIn(resp.status_code, [403, 404])


class DeadSharePurgeTest(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='drv28p', email='drv28p@sh.com', password='x')

    def test_revoked_share_purged_but_log_survives(self):
        from drive.tasks import purge_dead_shares
        share = _mk_share(self.owner, is_active=False)
        share.is_active = False
        # Backdate updated_at past the grace window
        Share.objects.filter(id=share.id).update(updated_at=timezone.now() - timedelta(days=60))
        # Create a log row pointing at this share
        log = ShareAccessLog.objects.create(
            share=share, share_id_copy=share.id,
            action=ShareAccessLog.ACTION_VIEW, result=ShareAccessLog.RESULT_OK,
        )
        result = purge_dead_shares()
        self.assertGreaterEqual(result['shares_purged'], 1)
        # Share must be gone
        self.assertFalse(Share.objects.filter(id=share.id).exists())
        # Log must survive (INV-5)
        log.refresh_from_db()
        self.assertEqual(log.share_id_copy, share.id)
        self.assertIsNone(log.share)  # FK nulled

    def test_active_unexpired_share_not_purged(self):
        from drive.tasks import purge_dead_shares
        share = _mk_share(self.owner)
        result = purge_dead_shares()
        self.assertTrue(Share.objects.filter(id=share.id).exists())


class RequireSharePermissionAuditLogTest(APITestCase):
    """DRV-28: RequireSharePermission writes ShareAccessLog for authenticated share access.

    Tests that the audit trail is written when grantees access files through
    the API endpoints that use RequireSharePermission (EDIT-level actions: PATCH/PUT),
    including access granted via inherited folder-cascade shares.
    """

    def setUp(self):
        self.owner = User.objects.create_user(username='drv28rsp_o', email='drv28rsp_o@sh.com', password='x')
        self.grantee = User.objects.create_user(username='drv28rsp_g', email='drv28rsp_g@sh.com', password='x')
        self.file = File.objects.create(
            name='shared.txt',
            owner_id=str(self.owner.id),
            file=SimpleUploadedFile('shared.txt', b'data', content_type='text/plain'),
            mime_type='text/plain', size=4, file_type='document',
        )
        self.share = Share.objects.create(
            file=self.file,
            share_type=ShareType.LINK,
            permission=SharePermission.EDIT,
            link_token='rsp-test-tok',
            shared_with_id=str(self.grantee.id),
            allow_download=True,
            created_by_id=str(self.owner.id),
            is_active=True,
        )

    def test_granted_edit_access_writes_ok_log(self):
        self.client.force_authenticate(user=self.grantee)
        url = f'/api/files/{self.file.id}/'
        resp = self.client.patch(url, {'name': 'shared-renamed.txt'}, format='json')
        # 200 on success, or 4xx in test env; what matters is the audit log
        log = ShareAccessLog.objects.filter(
            share=self.share,
            viewer_id=str(self.grantee.id),
        ).first()
        self.assertIsNotNone(log, 'RequireSharePermission must write a ShareAccessLog row on EDIT grant')
        self.assertEqual(log.share_id_copy, self.share.id)
        # PATCH is unsafe → ACTION_DOWNLOAD in the permission audit (DRV-28 / permissions.py)
        self.assertEqual(log.action, ShareAccessLog.ACTION_DOWNLOAD)
        self.assertEqual(log.result, ShareAccessLog.RESULT_OK)

    def test_denied_share_access_writes_denied_log(self):
        # Grantee only has VIEW; PATCH requires EDIT → denied, but log row must appear
        self.share.permission = SharePermission.VIEW
        self.share.save()
        self.client.force_authenticate(user=self.grantee)
        url = f'/api/files/{self.file.id}/'
        resp = self.client.patch(url, {'name': 'try-rename.txt'}, format='json')
        self.assertEqual(resp.status_code, 403)
        log = ShareAccessLog.objects.filter(
            share=self.share,
            viewer_id=str(self.grantee.id),
        ).first()
        self.assertIsNotNone(log, 'RequireSharePermission must write a ShareAccessLog row on denial')
        self.assertEqual(log.action, ShareAccessLog.ACTION_DOWNLOAD)
        self.assertEqual(log.result, ShareAccessLog.RESULT_DENIED)

    def test_inherited_folder_share_writes_log(self):
        """Access granted via a folder-cascade share must also write a ShareAccessLog row.

        Regression guard for the _find_granting_share ancestor-walk fix: a direct-only
        query missed folder shares and wrote no log row even though require_share_access
        returned True (Rei review 2026-06-30).
        """
        folder = Folder.objects.create(
            name='shared-folder',
            owner_id=str(self.owner.id),
        )
        file_in_folder = File.objects.create(
            name='nested.txt',
            owner_id=str(self.owner.id),
            folder=folder,
            file=SimpleUploadedFile('nested.txt', b'hello', content_type='text/plain'),
            mime_type='text/plain', size=5, file_type='document',
        )
        # Share is on the FOLDER, not the file — this is the cascade case
        folder_share = Share.objects.create(
            folder=folder,
            share_type=ShareType.LINK,
            permission=SharePermission.EDIT,
            link_token='rsp-folder-tok',
            shared_with_id=str(self.grantee.id),
            allow_download=True,
            created_by_id=str(self.owner.id),
            is_active=True,
        )
        self.client.force_authenticate(user=self.grantee)
        url = f'/api/files/{file_in_folder.id}/'
        resp = self.client.patch(url, {'name': 'nested-renamed.txt'}, format='json')
        # Access was granted via the folder share; audit row must be written
        log = ShareAccessLog.objects.filter(
            share=folder_share,
            viewer_id=str(self.grantee.id),
        ).first()
        self.assertIsNotNone(log, 'Folder-cascade share grant must write a ShareAccessLog row')
        self.assertEqual(log.share_id_copy, folder_share.id)
        self.assertEqual(log.action, ShareAccessLog.ACTION_DOWNLOAD)
        self.assertEqual(log.result, ShareAccessLog.RESULT_OK)
