"""DRV-37: paginated, ACL-scoped Drive activity API."""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import Activity, File, Share, SharePermission, ShareType
from drive.views import ActivityViewSet

User = get_user_model()


def _user(username, *, staff=False, superuser=False, org=None):
    user = User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='x',
        is_staff=staff,
        is_superuser=superuser,
    )
    if org:
        user.organization_id = org
    return user


def _file(owner, name):
    return File.objects.create(
        owner_id=str(owner.id),
        name=name,
        file=ContentFile(f'{name} bytes'.encode(), name=name),
        file_type='document',
        mime_type='text/plain',
        size=12,
        extension='txt',
    )


def _activity_request(user, query=None):
    factory = APIRequestFactory()
    request = factory.get('/drive/activity/', query or {})
    force_authenticate(request, user=user)
    return ActivityViewSet.as_view({'get': 'list'})(request)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv37-'))
class ActivityApiTests(TestCase):
    def setUp(self):
        self.owner = _user('drv37-owner')
        self.grantee = _user('drv37-grantee')
        self.outsider = _user('drv37-outsider')
        self.admin = _user('drv37-admin', staff=True)
        self.owned_file = _file(self.owner, 'owned.txt')
        self.shared_file = _file(self.owner, 'shared.txt')
        self.other_file = _file(self.outsider, 'other.txt')
        self.base = timezone.now().replace(microsecond=0)

        Share.objects.create(
            file=self.shared_file,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_id=str(self.grantee.id),
            created_by_id=str(self.owner.id),
        )
        self.owner_event = Activity.record_event(
            event_type='create',
            actor_id=self.owner.id,
            resource=self.owned_file,
            metadata={'case': 'owner-only'},
            timestamp=self.base,
        )
        self.shared_event = Activity.record_event(
            event_type='download',
            actor_id=self.grantee.id,
            resource=self.shared_file,
            metadata={'case': 'shared'},
            timestamp=self.base + timezone.timedelta(minutes=1),
        )
        self.other_event = Activity.record_event(
            event_type='create',
            actor_id=self.outsider.id,
            resource=self.other_file,
            metadata={'case': 'outsider'},
            timestamp=self.base + timezone.timedelta(minutes=2),
        )

    def _ids(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [item['id'] for item in response.data['items']]

    def test_owner_grantee_outsider_and_admin_visibility_boundaries(self):
        owner_ids = self._ids(_activity_request(self.owner))
        self.assertIn(str(self.owner_event.id), owner_ids)
        self.assertIn(str(self.shared_event.id), owner_ids)
        self.assertNotIn(str(self.other_event.id), owner_ids)

        grantee_ids = self._ids(_activity_request(self.grantee))
        self.assertEqual(grantee_ids, [str(self.shared_event.id)])

        outsider_ids = self._ids(_activity_request(self.outsider))
        self.assertEqual(outsider_ids, [str(self.other_event.id)])

        admin_ids = self._ids(_activity_request(self.admin))
        self.assertEqual(set(admin_ids), {
            str(self.owner_event.id), str(self.shared_event.id), str(self.other_event.id),
        })


    def test_same_org_non_grantee_cannot_see_user_or_link_share_activity(self):
        owner = _user('drv37-org-owner', org='org-1')
        grantee = _user('drv37-org-grantee', org='org-1')
        same_org_stranger = _user('drv37-org-stranger', org='org-1')
        user_file = _file(owner, 'user-only.txt')
        link_file = _file(owner, 'link-only.txt')
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
        user_event = Activity.record_event(
            event_type='share',
            actor_id=owner.id,
            resource=user_file,
            metadata={'case': 'user-share'},
            timestamp=self.base + timezone.timedelta(minutes=3),
        )
        link_event = Activity.record_event(
            event_type='share',
            actor_id=owner.id,
            resource=link_file,
            metadata={'case': 'link-share'},
            timestamp=self.base + timezone.timedelta(minutes=4),
        )

        self.assertEqual(self._ids(_activity_request(same_org_stranger)), [])
        self.assertIn(str(user_event.id), self._ids(_activity_request(grantee)))
        self.assertNotIn(str(link_event.id), self._ids(_activity_request(grantee)))

    def test_revoked_or_expired_share_removes_grantee_visibility_at_query_time(self):
        share = Share.objects.get(file=self.shared_file)
        share.is_active = False
        share.revoked_at = timezone.now()
        share.save(update_fields=['is_active', 'revoked_at', 'updated_at'])

        self.assertEqual(_activity_request(self.grantee).data, {'items': [], 'next_cursor': None})

        share.is_active = True
        share.revoked_at = None
        share.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        share.save(update_fields=['is_active', 'revoked_at', 'expires_at', 'updated_at'])

        self.assertEqual(_activity_request(self.grantee).data, {'items': [], 'next_cursor': None})

    def test_filters_are_composable_and_items_include_contract_fields(self):
        response = _activity_request(self.owner, {
            'resource_id': str(self.shared_file.id),
            'actor_id': str(self.grantee.id),
            'event_type': 'download,share',
            'from': (self.base + timezone.timedelta(seconds=30)).isoformat(),
            'to': (self.base + timezone.timedelta(minutes=2)).isoformat(),
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data['items']), 1)
        item = response.data['items'][0]
        for field in ('event_type', 'actor_id', 'resource_id', 'resource_type',
                      'resource_name', 'timestamp', 'metadata'):
            self.assertIn(field, item)
        self.assertEqual(item['event_type'], 'download')
        self.assertEqual(item['actor_id'], str(self.grantee.id))
        self.assertEqual(item['resource_id'], str(self.shared_file.id))
        self.assertEqual(item['resource_type'], 'file')
        self.assertEqual(item['resource_name'], 'shared.txt')
        self.assertEqual(item['metadata']['case'], 'shared')

    def test_keyset_pagination_uses_timestamp_then_id_cursor(self):
        newest = Activity.record_event(
            event_type='share',
            actor_id=self.owner.id,
            resource=self.shared_file,
            timestamp=self.base + timezone.timedelta(minutes=3),
        )
        first = _activity_request(self.owner, {'limit': '2'})
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual([row['id'] for row in first.data['items']], [
            str(newest.id), str(self.shared_event.id),
        ])
        self.assertIsNotNone(first.data['next_cursor'])

        second = _activity_request(self.owner, {'limit': '2', 'cursor': first.data['next_cursor']})
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual([row['id'] for row in second.data['items']], [str(self.owner_event.id)])
        self.assertIsNone(second.data['next_cursor'])

    def test_limit_is_capped_at_100(self):
        for idx in range(105):
            Activity.record_event(
                event_type='view',
                actor_id=self.owner.id,
                resource=self.owned_file,
                timestamp=self.base + timezone.timedelta(minutes=10, seconds=idx),
            )
        response = _activity_request(self.owner, {'limit': '500'})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data['items']), 100)
        self.assertIsNotNone(response.data['next_cursor'])
