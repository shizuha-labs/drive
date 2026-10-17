"""
DRV-14: Org-boundary enforcement on user/org shares (HLD §4.2 / §5 INV-3,INV-4).

H-3 / §4.2 — Every new user/org share must:
  1. Have organization_id set from the OWNER's trusted org claim (INV-3:
     authz-scope-provenance — never from a client-supplied header/body/query).
  2. Be classified same-org vs cross-org at create time.
     - Same-org (grantee_org_id == owner org) → allowed silently.
     - Cross-org (grantee_org_id != owner org) without allow_cross_org=True → 403
       cross_org_denied.
     - Cross-org WITH allow_cross_org=True → allowed + distinct audit event.
  3. Link shares are scope-agnostic → no cross-org gate.

Regression guard: a client-supplied organization_id is IGNORED; the server always
derives it from the owner's JWT claim (organization_id attribute on the user object).
"""
import uuid
from types import SimpleNamespace

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.authentication import ShizuhaJWTAuthentication
from drive.models import Activity, File, Folder, Share, ShareType
from drive.views import FileViewSet, FolderViewSet, ShareViewSet


def _user(uid, *, org=None, orgs=None, superuser=False, email=None):
    memberships = {str(v): 'member' for v in (orgs or ([org] if org else []))}
    return SimpleNamespace(
        id=uid, pk=uid, is_authenticated=True, is_superuser=superuser, is_staff=False,
        organization_id=org, organization_ids=list(memberships.keys()),
        email=email or f'{uid}@example.com',
        organization_memberships=memberships, organization_role=None,
    )


def _create_file(owner_id):
    return File.objects.create(
        owner_id=owner_id, name='test.txt', mime_type='text/plain', size=100)


def _post_share(user, file_id, payload, *, org_header=None):
    factory = APIRequestFactory()
    view = ShareViewSet.as_view({'post': 'create'})
    extra = {'HTTP_X_ORGANIZATION_ID': org_header} if org_header else {}
    request = factory.post('/api/shares/', payload, format='json', **extra)
    force_authenticate(request, user=user)
    return view(request)



def _share_list(user):
    factory = APIRequestFactory()
    view = ShareViewSet.as_view({'get': 'list'})
    request = factory.get('/api/shares/')
    force_authenticate(request, user=user)
    return view(request)


def _file_list(user):
    factory = APIRequestFactory()
    view = FileViewSet.as_view({'get': 'list'})
    request = factory.get('/api/files/')
    force_authenticate(request, user=user)
    return view(request)


def _file_search_shared(user, q):
    factory = APIRequestFactory()
    view = FileViewSet.as_view({'get': 'search'})
    request = factory.get('/api/files/search/', {'q': q, 'scope': 'shared'})
    force_authenticate(request, user=user)
    return view(request)


def _file_preview(user, file_id):
    factory = APIRequestFactory()
    view = FileViewSet.as_view({'get': 'preview'})
    request = factory.get(f'/api/files/{file_id}/preview/')
    force_authenticate(request, user=user)
    return view(request, pk=file_id)

def _folder_list(user, *, org_header=None):
    factory = APIRequestFactory()
    view = FolderViewSet.as_view({'get': 'list'})
    extra = {'HTTP_X_ORGANIZATION_ID': org_header} if org_header else {}
    request = factory.get('/api/folders/', **extra)
    force_authenticate(request, user=user)
    return view(request)


def _post_folder(user, payload, *, org_header=None):
    factory = APIRequestFactory()
    view = FolderViewSet.as_view({'post': 'create'})
    extra = {'HTTP_X_ORGANIZATION_ID': org_header} if org_header else {}
    request = factory.post('/api/folders/', payload, format='json', **extra)
    force_authenticate(request, user=user)
    return view(request)


class ExplicitOrgHeaderMembershipTests(TestCase):
    """HIVE-433: explicit wrong-org scopes must fail closed, not 200/201."""

    def setUp(self):
        self.user = _user('actor-a', org='146', orgs=['146'])

    def test_wrong_org_folder_list_is_forbidden(self):
        resp = _folder_list(self.user, org_header='147')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)

    def test_wrong_org_folder_create_is_forbidden_and_does_not_write(self):
        resp = _post_folder(self.user, {'name': 'should-not-exist'}, org_header='147')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertFalse(Folder.objects.filter(owner_id=self.user.id, name='should-not-exist').exists())

    def test_same_org_explicit_header_allows_existing_contract(self):
        resp = _post_folder(self.user, {'name': 'allowed'}, org_header='146')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertTrue(Folder.objects.filter(owner_id=self.user.id, name='allowed').exists())

    def test_wrong_org_share_create_is_forbidden_and_does_not_write(self):
        file = _create_file(self.user.id)
        resp = _post_share(self.user, file.id, {'file': str(file.id), 'share_type': ShareType.LINK}, org_header='147')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertFalse(Share.objects.filter(file=file).exists())


class OrgIdPopulatedFromOwnerTests(TestCase):
    """organization_id must always come from the owner's trusted JWT claim."""

    def setUp(self):
        self.owner_id = '111'
        self.owner_org = 'org-alpha'
        self.user = _user(self.owner_id, org=self.owner_org)
        self.file = _create_file(self.owner_id)

    def test_organization_id_set_from_owner_org(self):
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'colleague@org-alpha.com',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=self.file)
        self.assertEqual(share.organization_id, self.owner_org)

    def test_explicit_member_org_selects_tenant_for_multi_org_owner(self):
        self.user.organization_memberships = {
            self.owner_org: 'owner',
            'org-selected': 'member',
        }
        self.user.organization_ids = [self.owner_org, 'org-selected']
        resp = _post_share(
            self.user,
            self.file.id,
            {
                'file': str(self.file.id),
                'share_type': ShareType.ORGANIZATION,
                'grantee_org_id': 'org-selected',
            },
            org_header='org-selected',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(
            Share.objects.get(file=self.file).organization_id,
            'org-selected',
        )


    def test_share_create_response_includes_generated_id_for_revoke(self):
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'colleague@org-alpha.com',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=self.file)
        self.assertEqual(resp.data['id'], str(share.id))
        self.assertEqual(str(resp.data['file']), str(self.file.id))
        self.assertEqual(resp.data['shared_with_email'], 'colleague@org-alpha.com')
        self.assertEqual(resp.data['organization_id'], self.owner_org)

    def test_federated_user_preserves_organization_memberships_for_share_gates(self):
        user = ShizuhaJWTAuthentication().get_user({
            'user_id': 'fed-owner',
            'email': 'fed-owner@example.com',
            'username': 'fed-owner',
            'enabled_services': ['drive'],
            'organization_memberships': {self.owner_org: 'member'},
        })
        file = _create_file(user.id)
        resp = _post_share(user, file.id, {
            'file': str(file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'colleague@org-alpha.com',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=file)
        self.assertEqual(user.organization_id, self.owner_org)
        self.assertEqual(user.organization_ids, [self.owner_org])
        self.assertEqual(share.organization_id, self.owner_org)

    def test_client_supplied_organization_id_is_ignored(self):
        """The client cannot override organization_id via the request body (INV-3)."""
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'x@other.com',
            # Attempting to forge a different org — must be silently ignored.
            'organization_id': 'org-attacker',
        })
        # Request succeeds (no grantee_org_id provided → same-org assumed)
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))
        share = Share.objects.filter(file=self.file).first()
        # The persisted org must be the OWNER's org, never the attacker's.
        self.assertEqual(share.organization_id, self.owner_org)


    def test_user_share_create_by_email_is_visible_to_grantee_without_shared_with_id(self):
        grantee = _user('grantee-email', org=self.owner_org, email='grantee@example.com')
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'grantee@example.com',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=self.file)
        self.assertEqual(share.shared_with_email, 'grantee@example.com')
        self.assertIsNone(share.shared_with_id)

        shares = _share_list(grantee)
        self.assertEqual(shares.status_code, status.HTTP_200_OK, shares.data)
        share_rows = shares.data['results'] if isinstance(shares.data, dict) and 'results' in shares.data else shares.data
        self.assertEqual([row['id'] for row in share_rows], [str(share.id)])

        # PLAT-8918 (DRV-181): the default /api/files/ listing is OWNERSHIP-
        # scoped, so a grantee no longer sees shared items there — they live
        # on the explicit shared surfaces (search ?scope=shared, /api/shares/).
        # The email-matched grant (no shared_with_id) must surface there.
        found = _file_search_shared(grantee, 'test')
        self.assertEqual(found.status_code, status.HTTP_200_OK, found.data)
        rows = found.data['results'] if isinstance(found.data, dict) else found.data
        self.assertIn(str(self.file.id), [row['id'] for row in rows])

        preview = _file_preview(grantee, self.file.id)
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.data)
        self.assertEqual(preview.data['id'], str(self.file.id))

    def test_link_share_gets_owner_org(self):
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.LINK,
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=self.file)
        self.assertEqual(share.organization_id, self.owner_org)

    def test_no_org_on_user_gets_null_organization_id(self):
        user = _user('999', org=None)
        file = _create_file('999')
        resp = _post_share(user, file.id, {
            'file': str(file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'x@y.com',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=file)
        self.assertIsNone(share.organization_id)


class CrossOrgGateTests(TestCase):
    """Cross-org shares require explicit allow_cross_org=True (INV-4)."""

    def setUp(self):
        self.owner_id = '222'
        self.owner_org = 'org-beta'
        self.user = _user(self.owner_id, org=self.owner_org)
        self.file = _create_file(self.owner_id)

    def test_same_org_share_allowed_silently(self):
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'peer@beta.com',
            'grantee_org_id': self.owner_org,  # same org
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=self.file)
        self.assertFalse(share.allow_cross_org)
        # No cross_org key in audit event
        activity = Activity.objects.filter(file=self.file, action='share').first()
        self.assertNotIn('cross_org', (activity.details or {}))

    def test_cross_org_without_flag_is_denied(self):
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'external@other-org.com',
            'grantee_org_id': 'org-gamma',  # different org
            # allow_cross_org not set → should be denied
        })
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertIn('cross_org_denied', str(resp.data))
        self.assertFalse(Share.objects.filter(file=self.file).exists())

    def test_cross_org_with_flag_is_allowed_and_audited(self):
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'external@other-org.com',
            'grantee_org_id': 'org-gamma',
            'allow_cross_org': True,
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=self.file)
        self.assertTrue(share.allow_cross_org)
        # Distinct audit event with cross_org flag and grantee_org_id
        activity = Activity.objects.filter(file=self.file, action='share').first()
        self.assertIsNotNone(activity)
        self.assertTrue(activity.details.get('cross_org'))
        self.assertEqual(activity.details.get('grantee_org_id'), 'org-gamma')

    def test_link_share_skips_cross_org_gate(self):
        """Link shares are scope-agnostic; cross-org gate does not apply."""
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.LINK,
            'grantee_org_id': 'org-completely-different',
            # No allow_cross_org — but link share should still succeed
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_no_grantee_org_hint_assumes_same_org(self):
        """Without a grantee_org_id hint, the share is treated as same-org."""
        resp = _post_share(self.user, self.file.id, {
            'file': str(self.file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'unknown@unknown.com',
            # grantee_org_id omitted → no cross-org gate triggered
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        share = Share.objects.get(file=self.file)
        self.assertFalse(share.allow_cross_org)

    def test_owner_without_org_no_cross_org_gate(self):
        """If owner has no org claim, there is no org to compare against → no gate."""
        user = _user('333', org=None)
        file = _create_file('333')
        resp = _post_share(user, file.id, {
            'file': str(file.id),
            'share_type': ShareType.USER,
            'shared_with_email': 'x@y.com',
            'grantee_org_id': 'some-org',  # won't trigger gate — owner has no org
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
