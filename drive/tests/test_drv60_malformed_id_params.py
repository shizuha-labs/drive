"""DRV-60: malformed id query params must 400 at the boundary, never 500.

`GET /drive/api/files/?folder_id=not-a-uuid` used to reach ORM value coercion
unguarded and raise (Django 500). Both filter sites — the files-list path
(`visibility.apply_authorized_filters`) and the search path
(`_apply_search_filters`) — now validate at the API boundary.
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.views import FileViewSet
from drive.visibility import clean_int_param, clean_uuid_param

User = get_user_model()


class CleanParamHelperTests(TestCase):
    def test_valid_uuid_passes_through_canonical(self):
        u = uuid.uuid4()
        self.assertEqual(clean_uuid_param(str(u), 'folder_id'), str(u))

    def test_absent_or_empty_is_none(self):
        self.assertIsNone(clean_uuid_param(None, 'folder_id'))
        self.assertIsNone(clean_uuid_param('', 'folder_id'))
        self.assertIsNone(clean_int_param('', 'owner_id'))

    def test_malformed_uuid_raises_drf_validation_error(self):
        with self.assertRaises(DRFValidationError) as ctx:
            clean_uuid_param('not-a-uuid', 'folder_id')
        self.assertIn('folder_id', ctx.exception.detail)

    def test_malformed_int_raises_drf_validation_error(self):
        with self.assertRaises(DRFValidationError):
            clean_int_param('abc', 'owner_id')


class FilesListMalformedParamTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(username='drv60', password='x')
        self.view = FileViewSet.as_view({'get': 'list'})

    def _get(self, query):
        request = self.factory.get(f'/drive/api/files/?{query}')
        force_authenticate(request, user=self.user)
        return self.view(request)

    def test_malformed_folder_id_returns_400_not_500(self):
        response = self._get('folder_id=not-a-uuid')
        self.assertEqual(response.status_code, 400)
        self.assertIn('folder_id', str(response.data))

    def test_malformed_folder_alias_param_also_400(self):
        self.assertEqual(self._get('folder=zzz').status_code, 400)

    def test_malformed_owner_id_returns_400_not_500(self):
        response = self._get('owner_id=abc')
        self.assertEqual(response.status_code, 400)
        self.assertIn('owner_id', str(response.data))

    def test_wellformed_foreign_folder_id_still_empty_200(self):
        # The DRV-54 no-leak contract is untouched: well-formed but foreign
        # UUID intersects to an empty result, not an error.
        response = self._get('folder_id=00000000-0000-0000-0000-000000000000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get('count', 0), 0)

    def test_plain_list_still_works(self):
        self.assertEqual(self._get('').status_code, 200)
