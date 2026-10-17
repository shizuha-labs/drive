"""DRV-54 shared visibility service security/correctness tests."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drive.models import Activity, File, FileActivity, Folder, Share, SharePermission, ShareType
from drive.permissions import accessible_object_ids, require_share_access
from drive.visibility import visible_activity, visible_file_activity, visible_files, visible_folders


def _user(username, org=None):
    user = User.objects.create_user(username=username, email=f'{username}@example.com', password='x')
    if org:
        user.organization_id = org
    return user


class DRV54VisibilityServiceTests(TestCase):
    def _folder(self, owner, name, parent=None):
        return Folder.objects.create(name=name, owner_id=str(owner.id), parent=parent)

    def _file(self, owner, name, folder=None, **kwargs):
        return File.objects.create(
            name=name,
            owner_id=str(owner.id),
            folder=folder,
            file_type=kwargs.get('file_type', 'document'),
            mime_type=kwargs.get('mime_type', 'text/plain'),
            size=kwargs.get('size', 1),
            is_starred=kwargs.get('is_starred', False),
            is_trashed=kwargs.get('is_trashed', False),
        )

    def test_folder_share_inheritance_reaches_ten_nested_levels_without_sibling_leak(self):
        owner = _user('owner')
        recipient = _user('recipient')
        root = self._folder(owner, 'root')
        current = root
        for depth in range(10):
            current = self._folder(owner, f'level-{depth}', parent=current)
        inherited = self._file(owner, 'deep.txt', folder=current)
        sibling = self._folder(owner, 'sibling')
        sibling_file = self._file(owner, 'sibling.txt', folder=sibling)

        Share.objects.create(
            folder=root,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_id=str(recipient.id),
            created_by_id=str(owner.id),
        )

        self.assertIn(inherited, visible_files(recipient))
        self.assertNotIn(sibling_file, visible_files(recipient))
        self.assertIn(current, visible_folders(recipient))
        self.assertNotIn(sibling, visible_folders(recipient))

    def test_same_name_and_content_do_not_leak_across_users_or_orgs(self):
        owner_a = _user('owner-a')
        owner_b = _user('owner-b')
        direct_user = _user('direct')
        org_user = _user('org-member', org='org-1')
        other_user = _user('other', org='org-2')
        direct_file = self._file(owner_a, 'budget.txt')
        org_file = self._file(owner_a, 'budget.txt')
        private_file = self._file(owner_b, 'budget.txt')

        Share.objects.create(
            file=direct_file,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_id=str(direct_user.id),
            created_by_id=str(owner_a.id),
        )
        Share.objects.create(
            file=org_file,
            share_type=ShareType.ORGANIZATION,
            permission=SharePermission.VIEW,
            organization_id='org-1',
            created_by_id=str(owner_a.id),
        )

        self.assertEqual(set(visible_files(direct_user)), {direct_file})
        self.assertEqual(set(visible_files(org_user)), {org_file})
        self.assertEqual(set(visible_files(other_user)), set())
        self.assertNotIn(private_file, visible_files(direct_user))

    def test_same_org_non_grantee_does_not_receive_user_or_link_share(self):
        owner = _user('owner', org='org-1')
        grantee = _user('grantee', org='org-1')
        same_org_non_grantee = _user('same-org-stranger', org='org-1')
        user_file = self._file(owner, 'user-only.txt')
        link_file = self._file(owner, 'link-only.txt')

        # ShareViewSet stores the owner's organization_id on non-org rows for
        # audit/cross-org policy, but visibility must NOT treat that as an org grant.
        Share.objects.create(
            file=user_file,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_id=str(grantee.id),
            organization_id='org-1',
            created_by_id=str(owner.id),
        )
        Share.objects.create(
            file=link_file,
            share_type=ShareType.LINK,
            permission=SharePermission.VIEW,
            organization_id='org-1',
            created_by_id=str(owner.id),
        )

        self.assertEqual(set(visible_files(grantee)), {user_file})
        self.assertEqual(set(visible_files(same_org_non_grantee)), set())


    def test_same_org_non_grantee_is_not_findable_or_authorized_for_user_or_link_share(self):
        owner = _user('perm-owner', org='org-1')
        grantee = _user('perm-grantee', org='org-1')
        same_org_non_grantee = _user('perm-stranger', org='org-1')
        user_file = self._file(owner, 'editable-user-share.txt')
        link_file = self._file(owner, 'editable-link-share.txt')

        Share.objects.create(
            file=user_file,
            share_type=ShareType.USER,
            permission=SharePermission.EDIT,
            shared_with_id=str(grantee.id),
            organization_id='org-1',
            created_by_id=str(owner.id),
        )
        Share.objects.create(
            file=link_file,
            share_type=ShareType.LINK,
            permission=SharePermission.EDIT,
            organization_id='org-1',
            created_by_id=str(owner.id),
        )

        self.assertIn(user_file.id, accessible_object_ids(grantee, File))
        self.assertTrue(require_share_access(grantee, user_file, SharePermission.EDIT))
        self.assertNotIn(user_file.id, accessible_object_ids(same_org_non_grantee, File))
        self.assertNotIn(link_file.id, accessible_object_ids(same_org_non_grantee, File))
        self.assertFalse(require_share_access(same_org_non_grantee, user_file, SharePermission.VIEW))
        self.assertFalse(require_share_access(same_org_non_grantee, link_file, SharePermission.VIEW))


    def test_email_only_user_share_grants_direct_and_inherited_visibility_until_revoked(self):
        owner = _user('email-owner', org='org-1')
        recipient = _user('email-recipient', org='org-1')
        same_org_non_grantee = _user('email-stranger', org='org-1')
        direct = self._file(owner, 'email-direct.txt', is_starred=True)
        root = self._folder(owner, 'email-root')
        current = root
        for depth in range(10):
            current = self._folder(owner, f'email-level-{depth}', parent=current)
        inherited = self._file(owner, 'email-leaf.txt', folder=current)
        sibling = self._folder(owner, 'email-sibling')
        sibling_file = self._file(owner, 'email-sibling.txt', folder=sibling)
        private = self._file(owner, 'email-private.txt')

        direct_share = Share.objects.create(
            file=direct,
            share_type=ShareType.USER,
            permission=SharePermission.EDIT,
            shared_with_email=recipient.email.upper(),
            organization_id='org-1',
            created_by_id=str(owner.id),
        )
        folder_share = Share.objects.create(
            folder=root,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_email=recipient.email,
            organization_id='org-1',
            created_by_id=str(owner.id),
        )
        activity = Activity.objects.create(
            user_id=str(recipient.id),
            file=direct,
            action='view',
            details={'source': 'drv65'},
        )
        file_activity = FileActivity.objects.create(
            user_id=str(recipient.id),
            file=direct,
            action=FileActivity.ACTION_OPEN,
        )

        self.assertIsNone(direct_share.shared_with_id)
        self.assertEqual(set(visible_files(recipient)), {direct, inherited})
        self.assertIn(current, visible_folders(recipient))
        self.assertNotIn(sibling, visible_folders(recipient))
        self.assertNotIn(sibling_file, visible_files(recipient))
        self.assertNotIn(private, visible_files(recipient))
        self.assertEqual(set(visible_files(same_org_non_grantee)), set())
        self.assertIn(direct.id, accessible_object_ids(recipient, File))
        self.assertIn(inherited.id, accessible_object_ids(recipient, File))
        self.assertTrue(require_share_access(recipient, direct, SharePermission.EDIT))
        self.assertIn(activity, visible_activity(recipient))
        self.assertIn(file_activity, visible_file_activity(recipient))

        Share.objects.filter(pk__in=[direct_share.pk, folder_share.pk]).update(
            is_active=False,
            revoked_at=timezone.now(),
        )

        self.assertEqual(set(visible_files(recipient)), set())
        self.assertNotIn(activity, visible_activity(recipient))
        self.assertNotIn(file_activity, visible_file_activity(recipient))
        self.assertTrue(Activity.objects.filter(pk=activity.pk).exists())
        self.assertTrue(FileActivity.objects.filter(pk=file_activity.pk).exists())
        self.assertTrue(File.objects.filter(pk=direct.pk, is_starred=True).exists())

    def test_email_only_user_share_without_org_hint_does_not_cross_org_gate(self):
        owner = _user('cross-owner', org='org-a')
        recipient = _user('cross-recipient', org='org-b')
        file_obj = self._file(owner, 'cross-org-secret.txt')

        # API-created shape when the owner omitted grantee_org_id: the row is
        # tagged with the owner's org and is not explicitly cross-org.
        share = Share.objects.create(
            file=file_obj,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_email=recipient.email,
            organization_id='org-a',
            allow_cross_org=False,
            created_by_id=str(owner.id),
        )
        activity = Activity.objects.create(
            user_id=str(recipient.id),
            file=file_obj,
            action='view',
            details={'source': 'drv65-cross-org-regression'},
        )
        file_activity = FileActivity.objects.create(
            user_id=str(recipient.id),
            file=file_obj,
            action=FileActivity.ACTION_OPEN,
        )

        self.assertEqual(set(visible_files(recipient)), set())
        self.assertNotIn(file_obj.id, accessible_object_ids(recipient, File))
        self.assertFalse(require_share_access(recipient, file_obj, SharePermission.VIEW))
        self.assertNotIn(activity, visible_activity(recipient))
        self.assertNotIn(file_activity, visible_file_activity(recipient))

        client = APIClient()
        client.force_authenticate(recipient)
        list_resp = client.get('/api/files/')
        self.assertEqual(list_resp.status_code, 200, list_resp.content)
        self.assertNotIn(str(file_obj.id), str(list_resp.data))
        search_resp = client.get('/api/files/search/', {'q': 'cross-org-secret'})
        self.assertEqual(search_resp.status_code, 200, search_resp.content)
        self.assertEqual(search_resp.data['results'], [])
        recent_resp = client.get('/api/files/recent/')
        self.assertEqual(recent_resp.status_code, 200, recent_resp.content)
        self.assertNotIn(str(file_obj.id), str(recent_resp.data))
        preview_resp = client.get(f'/api/files/{file_obj.id}/preview/')
        self.assertEqual(preview_resp.status_code, 404)

        share.allow_cross_org = True
        share.save(update_fields=['allow_cross_org'])
        self.assertIn(file_obj, visible_files(recipient))
        preview_resp = client.get(f'/api/files/{file_obj.id}/preview/')
        self.assertEqual(preview_resp.status_code, 200, preview_resp.content)

    def test_revoked_and_expired_shares_drop_recent_and_starred_without_deleting_rows(self):
        owner = _user('owner')
        recipient = _user('recipient')
        file_obj = self._file(owner, 'revoked.txt', is_starred=True)
        share = Share.objects.create(
            file=file_obj,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_id=str(recipient.id),
            created_by_id=str(owner.id),
        )
        activity = Activity.objects.create(
            user_id=str(recipient.id),
            file=file_obj,
            action='view',
            details={'source': 'test'},
        )
        self.assertIn(file_obj, visible_files(recipient))
        self.assertIn(activity, visible_activity(recipient))

        share.is_active = False
        share.revoked_at = timezone.now()
        share.save(update_fields=['is_active', 'revoked_at'])

        self.assertNotIn(file_obj, visible_files(recipient))
        self.assertNotIn(activity, visible_activity(recipient))
        self.assertTrue(Activity.objects.filter(pk=activity.pk).exists())
        self.assertTrue(File.objects.filter(pk=file_obj.pk, is_starred=True).exists())

        share.is_active = True
        share.revoked_at = None
        share.expires_at = timezone.now() - timedelta(seconds=1)
        share.save(update_fields=['is_active', 'revoked_at', 'expires_at'])
        self.assertNotIn(file_obj, visible_files(recipient))
