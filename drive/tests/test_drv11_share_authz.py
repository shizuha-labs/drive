"""
DRV-11: Drive share authorization hardening.

C-1 — a user cannot mint a share (incl. a public download link) for a file/folder
      they do not own. Enforced at the serializer (ShareCreateSerializer.validate)
      AND re-asserted in ShareViewSet.perform_create (defense in depth).
C-2 — is_org_admin_or_owner is resource-org-scoped: an org admin/owner of org A is
      NOT granted access to a resource in org B; with no resource org id the
      role-only bypass does not apply (fail closed); superusers always pass.
"""
from types import SimpleNamespace

from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from drive.models import File, Folder, ShareType, SharePermission
from drive.permissions import is_org_admin_or_owner
from drive.serializers import ShareCreateSerializer
from drive.views import ShareViewSet


def _user(uid, *, role=None, org=None, superuser=False):
    return SimpleNamespace(
        id=uid, pk=uid, is_superuser=superuser,
        organization_role=role, organization_id=org,
    )


class C2OrgScopeTests(TestCase):
    def test_superuser_always_passes(self):
        self.assertTrue(is_org_admin_or_owner(_user('1', superuser=True), None))
        self.assertTrue(is_org_admin_or_owner(_user('1', superuser=True), 'orgB'))

    def test_admin_same_org_passes(self):
        u = _user('1', role='admin', org='orgA')
        self.assertTrue(is_org_admin_or_owner(u, 'orgA'))

    def test_admin_cross_org_denied(self):
        # The C-2 bug: org-A admin must NOT reach an org-B resource.
        u = _user('1', role='admin', org='orgA')
        self.assertFalse(is_org_admin_or_owner(u, 'orgB'))

    def test_admin_no_resource_org_fails_closed(self):
        # File/Folder carry no organization_id → role-only bypass must NOT grant.
        u = _user('1', role='owner', org='orgA')
        self.assertFalse(is_org_admin_or_owner(u, None))

    def test_non_admin_denied(self):
        self.assertFalse(
            is_org_admin_or_owner(_user('1', role='member', org='orgA'), 'orgA'))


class C1ShareOwnershipTests(TestCase):
    def setUp(self):
        self.owner = '100'
        self.attacker = '200'
        self.victim_file = File.objects.create(
            owner_id=self.owner, name='secret.txt', mime_type='text/plain', size=10)
        self.victim_folder = Folder.objects.create(owner_id=self.owner, name='secret')

    def _ctx(self, uid):
        return {'request': SimpleNamespace(user=_user(uid))}

    def _serializer(self, validated):
        return SimpleNamespace(validated_data=validated)

    def _viewset(self, uid):
        vs = ShareViewSet()
        vs.request = SimpleNamespace(user=_user(uid))
        return vs

    # --- serializer layer ---
    def test_serializer_rejects_sharing_non_owned_file(self):
        s = ShareCreateSerializer(
            data={'file': str(self.victim_file.id),
                  'share_type': ShareType.LINK, 'allow_download': True},
            context=self._ctx(self.attacker))
        self.assertFalse(s.is_valid())
        self.assertIn('not found', str(s.errors).lower())

    def test_serializer_allows_owner(self):
        s = ShareCreateSerializer(
            data={'file': str(self.victim_file.id), 'share_type': ShareType.LINK},
            context=self._ctx(self.owner))
        self.assertTrue(s.is_valid(), s.errors)

    # --- perform_create defense-in-depth layer ---
    def test_perform_create_guard_blocks_non_owned_file(self):
        vs = self._viewset(self.attacker)
        with self.assertRaises(PermissionDenied):
            vs.perform_create(self._serializer({
                'file': self.victim_file.id, 'folder': None,
                'share_type': ShareType.LINK, 'permission': SharePermission.VIEW,
                'allow_download': True,
            }))

    def test_perform_create_guard_blocks_non_owned_folder(self):
        vs = self._viewset(self.attacker)
        with self.assertRaises(PermissionDenied):
            vs.perform_create(self._serializer({
                'file': None, 'folder': self.victim_folder.id,
                'share_type': ShareType.LINK, 'permission': SharePermission.VIEW,
                'allow_download': True,
            }))

    def test_perform_create_allows_owner(self):
        vs = self._viewset(self.owner)
        share = vs.perform_create(self._serializer({
            'file': self.victim_file.id, 'folder': None,
            'share_type': ShareType.LINK, 'permission': SharePermission.VIEW,
            'allow_download': True,
        }))
        self.assertIsNotNone(share)
        self.assertEqual(str(share.file_id), str(self.victim_file.id))
