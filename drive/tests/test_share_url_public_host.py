"""Public share links must use shizuha.com, not the inbound Host / Tailscale name."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from drive.models import ShareType
from drive.serializers import ShareSerializer


class PublicShareUrlTests(SimpleTestCase):
    def test_defaults_to_https_shizuha_com(self):
        obj = SimpleNamespace(share_type=ShareType.LINK, link_token='tok123')
        env = {k: v for k, v in os.environ.items()
               if k not in ('SHIZUHA_PUBLIC_HOST', 'SHIZUHA_PUBLIC_SCHEME')}
        with patch.dict(os.environ, env, clear=True):
            url = ShareSerializer().get_share_url(obj)
        self.assertEqual(url, 'https://shizuha.com/drive/share/tok123')

    def test_uses_configured_public_host(self):
        obj = SimpleNamespace(share_type=ShareType.LINK, link_token='abc')
        with patch.dict(os.environ, {
            'SHIZUHA_PUBLIC_HOST': 'shizuha.com',
            'SHIZUHA_PUBLIC_SCHEME': 'https',
        }, clear=False):
            url = ShareSerializer().get_share_url(obj)
        self.assertEqual(url, 'https://shizuha.com/drive/share/abc')

    def test_non_link_share_has_no_url(self):
        obj = SimpleNamespace(share_type=ShareType.USER, link_token='tok')
        self.assertIsNone(ShareSerializer().get_share_url(obj))
