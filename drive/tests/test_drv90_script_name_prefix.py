"""DRV-90: DRF pagination next/previous must carry the /drive prefix + https.

The ingress strips /drive (rewrite-target), so Django never sees it and DRF's
build_absolute_uri() emits prefix-less SPA-bound URLs. ScriptNameMiddleware
rewrites request.path from DRIVE_URL_PREFIX (default '' — root-path safe) and
SECURE_PROXY_SSL_HEADER restores https behind the TLS-terminating ingress.
"""
import os
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings


class ScriptNameMiddlewareTests(TestCase):
    def _via_middleware(self, path='/api/folders/?page=2', env=None):
        factory = RequestFactory()
        request = factory.get(path)
        from drivemanager.middleware import ScriptNameMiddleware
        with mock.patch.dict(os.environ, env or {}, clear=False):
            ScriptNameMiddleware(lambda r: None)(request)
        return request

    def test_prefix_and_scheme_in_absolute_uri(self):
        # DRV-90: with DRIVE_URL_PREFIX=/drive + X-Forwarded-Proto: https the
        # absolute URI must carry the public mount prefix and https scheme.
        request = self._via_middleware(env={'DRIVE_URL_PREFIX': '/drive'})
        request.META['HTTP_X_FORWARDED_PROTO'] = 'https'
        with override_settings(
            SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        ):
            uri = request.build_absolute_uri()
        self.assertTrue(uri.startswith('https://'), uri)
        self.assertIn('/drive/api/folders/', uri)
        self.assertIn('page=2', uri)

    def test_middleware_sets_path_from_env_prefix(self):
        request = self._via_middleware(env={'DRIVE_URL_PREFIX': '/drive'})
        self.assertEqual(request.path, '/drive/api/folders/')

    def test_middleware_trailing_slash_normalized(self):
        request = self._via_middleware(env={'DRIVE_URL_PREFIX': '/drive/'})
        self.assertEqual(request.path, '/drive/api/folders/')

    def test_middleware_falls_back_when_prefix_unset(self):
        # DRV-90 acceptance #4: served at the root path (dev/local, no prefix),
        # the middleware must NOT invent a /drive prefix.
        request = self._via_middleware(env={})
        self.assertEqual(request.path, '/api/folders/')
        self.assertNotIn('/drive', request.path)
