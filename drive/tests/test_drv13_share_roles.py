"""
DRV-13: granular share-role enforcement + object-owner soft-revoke.

H-1 — require_share_access is the single precedence helper (HLD §4.1): owner→EDIT;
      same-org admin→EDIT; active+unexpired user-share→its level; active+unexpired
      org-share→its level iff org matches; else DENY. Re-validated at request time
      (INV-6: is_active + expiry + org). Wired into File/Folder mutate paths.
M-4 — ShareViewSet.destroy enforces object-owner and SOFT-revokes (is_active=False
      + revoked_at), never a grantee hard-delete (INV-5).
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import File, Folder, Share, ShareType, SharePermission
from drive.permissions import _active_shares_for, effective_share_level, require_share_access
from drive.views import FileViewSet, ShareViewSet

R = {SharePermission.VIEW: 1, SharePermission.COMMENT: 2, SharePermission.EDIT: 3}


def _user(uid, *, role=None, org=None, superuser=False):
    return SimpleNamespace(
        id=uid, pk=uid, is_superuser=superuser,
        is_authenticated=True,
        organization_role=role, organization_id=org,
    )


def _share(obj, *, perm, with_id=None, org=None, active=True, expires=None,
           creator='100'):
    kwargs = dict(
        share_type=ShareType.USER if with_id else ShareType.ORGANIZATION,
        permission=perm, created_by_id=creator, is_active=active,
        shared_with_id=with_id, organization_id=org, expires_at=expires,
    )
    if isinstance(obj, File):
        kwargs['file'] = obj
    else:
        kwargs['folder'] = obj
    return Share.objects.create(**kwargs)


class EffectiveShareLevelTests(TestCase):
    def setUp(self):
        self.owner = '100'
        self.grantee = '200'
        self.stranger = '300'
        self.file = File.objects.create(
            owner_id=self.owner, name='doc.txt', mime_type='text/plain', size=10)

    def test_owner_gets_edit(self):
        self.assertEqual(effective_share_level(_user(self.owner), self.file),
                         R[SharePermission.EDIT])

    def test_edit_share_grants_edit(self):
        _share(self.file, perm=SharePermission.EDIT, with_id=self.grantee)
        u = _user(self.grantee)
        self.assertTrue(require_share_access(u, self.file, SharePermission.EDIT))

    def test_comment_share_cannot_edit(self):
        _share(self.file, perm=SharePermission.COMMENT, with_id=self.grantee)
        u = _user(self.grantee)
        self.assertTrue(require_share_access(u, self.file, SharePermission.COMMENT))
        self.assertFalse(require_share_access(u, self.file, SharePermission.EDIT))

    def test_view_share_cannot_comment_or_edit(self):
        _share(self.file, perm=SharePermission.VIEW, with_id=self.grantee)
        u = _user(self.grantee)
        self.assertTrue(require_share_access(u, self.file, SharePermission.VIEW))
        self.assertFalse(require_share_access(u, self.file, SharePermission.COMMENT))
        self.assertFalse(require_share_access(u, self.file, SharePermission.EDIT))

    def test_non_grantee_denied(self):
        _share(self.file, perm=SharePermission.EDIT, with_id=self.grantee)
        self.assertEqual(effective_share_level(_user(self.stranger), self.file), 0)
        self.assertFalse(
            require_share_access(_user(self.stranger), self.file, SharePermission.VIEW))

    def test_inactive_share_denied(self):
        _share(self.file, perm=SharePermission.EDIT, with_id=self.grantee, active=False)
        self.assertEqual(effective_share_level(_user(self.grantee), self.file), 0)

    def test_expired_share_denied_inv6(self):
        _share(self.file, perm=SharePermission.EDIT, with_id=self.grantee,
               expires=timezone.now() - timedelta(hours=1))
        self.assertEqual(effective_share_level(_user(self.grantee), self.file), 0)

    def test_org_share_matching_org_grants(self):
        _share(self.file, perm=SharePermission.EDIT, org='orgX')
        self.assertTrue(require_share_access(
            _user(self.grantee, org='orgX'), self.file, SharePermission.EDIT))

    def test_org_share_mismatched_org_denied(self):
        _share(self.file, perm=SharePermission.EDIT, org='orgX')
        self.assertEqual(
            effective_share_level(_user(self.grantee, org='orgY'), self.file), 0)

    def test_folder_share_inherits_to_file(self):
        folder = Folder.objects.create(owner_id=self.owner, name='shared')
        f = File.objects.create(owner_id=self.owner, name='in.txt',
                                mime_type='text/plain', size=5, folder=folder)
        _share(folder, perm=SharePermission.EDIT, with_id=self.grantee)
        self.assertTrue(require_share_access(
            _user(self.grantee), f, SharePermission.EDIT))

    def test_active_shares_batches_deep_ancestor_chain(self):
        parent = None
        for depth in range(20):
            parent = Folder.objects.create(
                owner_id=self.owner,
                name=f'level-{depth}',
                parent=parent,
            )
        f = File.objects.create(
            owner_id=self.owner,
            name='deep.txt',
            mime_type='text/plain',
            size=5,
            folder=parent,
        )
        root = parent
        while root.parent_id:
            root = Folder.objects.get(pk=root.parent_id)
        _share(root, perm=SharePermission.EDIT, with_id=self.grantee)

        with self.assertNumQueries(2):
            shares = _active_shares_for(f)

        self.assertEqual([share.permission for share in shares], [SharePermission.EDIT])
        self.assertTrue(require_share_access(
            _user(self.grantee), f, SharePermission.EDIT))


class FileDetailTargetedAuthorizationTests(TestCase):
    """Detail reads must not expand every file under every shared folder."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = _user('detail-owner')
        self.grantee = _user('detail-grantee')
        self.stranger = _user('detail-stranger')
        self.root = Folder.objects.create(owner_id=self.owner.id, name='root')
        self.child = Folder.objects.create(
            owner_id=self.owner.id,
            name='child',
            parent=self.root,
        )
        self.file = File.objects.create(
            owner_id=self.owner.id,
            name='detail.txt',
            mime_type='text/plain',
            size=10,
            folder=self.child,
        )

    def _retrieve(self, user):
        request = self.factory.get(f'/api/files/{self.file.id}/')
        force_authenticate(request, user=user)
        return FileViewSet.as_view({'get': 'retrieve'})(
            request,
            pk=str(self.file.id),
        )

    @patch(
        'drive.views.accessible_object_ids',
        side_effect=AssertionError('detail lookup expanded the shared tree'),
    )
    def test_owner_detail_read_does_not_expand_shared_tree(self, expand):
        response = self._retrieve(self.owner)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], str(self.file.id))
        expand.assert_not_called()

    @patch(
        'drive.views.accessible_object_ids',
        side_effect=AssertionError('detail lookup expanded the shared tree'),
    )
    def test_inherited_share_detail_read_is_target_scoped(self, expand):
        _share(
            self.root,
            perm=SharePermission.VIEW,
            with_id=self.grantee.id,
            creator=self.owner.id,
        )

        response = self._retrieve(self.grantee)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], str(self.file.id))
        expand.assert_not_called()

    @patch(
        'drive.views.accessible_object_ids',
        side_effect=AssertionError('detail lookup expanded the shared tree'),
    )
    def test_unshared_detail_stays_undiscoverable_without_expansion(self, expand):
        response = self._retrieve(self.stranger)

        self.assertEqual(response.status_code, 404, response.data)
        expand.assert_not_called()


class ShareDestroySoftRevokeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = '100'
        self.grantee = '200'
        self.file = File.objects.create(
            owner_id=self.owner, name='doc.txt', mime_type='text/plain', size=10)
        self.share = _share(self.file, perm=SharePermission.EDIT,
                            with_id=self.grantee, creator=self.owner)

    def _destroy_as(self, user):
        view = ShareViewSet.as_view({'delete': 'destroy'})
        req = self.factory.delete(f'/api/shares/{self.share.id}/')
        force_authenticate(req, user=user)
        return view(req, pk=str(self.share.id))

    def test_grantee_cannot_destroy(self):
        # The M-4 bug: a grantee (visible via shared_with_id) must not delete a
        # share they don't own.
        response = self._destroy_as(_user(self.grantee))
        self.assertEqual(response.status_code, 403)
        self.share.refresh_from_db()
        self.assertTrue(self.share.is_active)  # untouched

    def test_owner_soft_revoke_preserves_row(self):
        resp = self._destroy_as(_user(self.owner))
        self.assertEqual(resp.status_code, 204)
        self.share.refresh_from_db()  # row still exists (INV-5)
        self.assertFalse(self.share.is_active)
        self.assertIsNotNone(self.share.revoked_at)
