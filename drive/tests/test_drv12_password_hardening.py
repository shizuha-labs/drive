"""
DRV-12: Link-share password hardening.

H-2 — passwords stored via make_password (PBKDF2), verified constant-time via
      check_password; legacy unsalted sha256 values verify once then upgrade-on-verify.
M-2 — GET never reads the password (returns password_required); verification is
      POST-body only.
M-3 — brute-force throttle per (token, client-ip) → 429 after the failure threshold.
"""
import hashlib
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from drive.models import File, Share, ShareType
from drive.views import (
    PublicShareView,
    _verify_share_password,
    _share_pw_rate_limited,
    _share_pw_record_failure,
    _SHARE_PW_MAX_FAILURES,
)


_share_counter = 0


def _make_share(password_value=None, *, token=None):
    global _share_counter
    _share_counter += 1
    f = File.objects.create(owner_id='100', name='secret.txt', mime_type='text/plain', size=10)
    return Share.objects.create(
        file_id=f.id,
        share_type=ShareType.LINK,
        created_by_id='100',
        link_token=token or f'tok-drv12-{_share_counter}',
        link_password=password_value or '',
    )


class VerifyPasswordTests(TestCase):
    def test_make_password_roundtrip(self):
        share = _make_share(make_password('hunter2'))
        self.assertTrue(_verify_share_password(share, 'hunter2'))
        self.assertFalse(_verify_share_password(share, 'wrong'))

    def test_no_password_share_always_ok(self):
        self.assertTrue(_verify_share_password(_make_share(''), 'anything'))
        self.assertTrue(_verify_share_password(_make_share(''), None))

    def test_legacy_sha256_verifies_then_upgrades(self):
        legacy = hashlib.sha256(b'hunter2').hexdigest()  # bare hex, no '$'
        share = _make_share(legacy)
        self.assertNotIn('$', share.link_password)
        # correct legacy password verifies...
        self.assertTrue(_verify_share_password(share, 'hunter2'))
        # ...and is transparently rehashed to a salted Django hash (upgrade-on-verify)
        share.refresh_from_db()
        self.assertTrue(share.link_password.startswith('pbkdf2_'))
        self.assertNotEqual(share.link_password, legacy)
        # still verifies after upgrade
        self.assertTrue(_verify_share_password(share, 'hunter2'))

    def test_legacy_wrong_password_rejected_not_upgraded(self):
        legacy = hashlib.sha256(b'hunter2').hexdigest()
        share = _make_share(legacy)
        self.assertFalse(_verify_share_password(share, 'wrong'))
        share.refresh_from_db()
        self.assertEqual(share.link_password, legacy)  # unchanged on failure


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()

    def test_blocks_after_threshold(self):
        req = self.factory.post('/public/tok-drv12/', {}, format='json')
        token = 'tok-drv12'
        self.assertFalse(_share_pw_rate_limited(token, req))
        for _ in range(_SHARE_PW_MAX_FAILURES):
            _share_pw_record_failure(token, req)
        self.assertTrue(_share_pw_rate_limited(token, req))


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PublicShareViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = PublicShareView.as_view()

    def test_get_password_share_never_returns_details(self):
        # M-2: GET on a password share returns password_required, never details,
        # and never reads a ?password= query param.
        _make_share(make_password('hunter2'), token='tok-drv12')
        req = self.factory.get('/public/tok-drv12/?password=hunter2')
        resp = self.view(req, token='tok-drv12')
        self.assertEqual(resp.status_code, 401)
        self.assertTrue(resp.data.get('password_required'))
        self.assertNotIn('type', resp.data)  # no details leaked

    def test_get_non_password_share_returns_details(self):
        _make_share('', token='tok-drv12')
        resp = self.view(self.factory.get('/public/tok-drv12/'), token='tok-drv12')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['type'], 'file')

    def test_post_unlock_requires_then_accepts_password(self):
        _make_share(make_password('hunter2'), token='tok-drv12')
        # no password -> password_required
        r1 = self.view(self.factory.post('/public/tok-drv12/', {'action': 'unlock'}, format='json'), token='tok-drv12')
        self.assertEqual(r1.status_code, 401)
        self.assertTrue(r1.data.get('password_required'))
        # wrong password -> invalid
        r2 = self.view(self.factory.post('/public/tok-drv12/', {'password': 'nope', 'action': 'unlock'}, format='json'), token='tok-drv12')
        self.assertEqual(r2.status_code, 401)
        # correct password -> details
        r3 = self.view(self.factory.post('/public/tok-drv12/', {'password': 'hunter2', 'action': 'unlock'}, format='json'), token='tok-drv12')
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.data['type'], 'file')

    def test_post_brute_force_returns_429(self):
        _make_share(make_password('hunter2'), token='tok-drv12')
        for _ in range(_SHARE_PW_MAX_FAILURES):
            self.view(self.factory.post('/public/tok-drv12/', {'password': 'x', 'action': 'unlock'}, format='json'), token='tok-drv12')
        blocked = self.view(self.factory.post('/public/tok-drv12/', {'password': 'x', 'action': 'unlock'}, format='json'), token='tok-drv12')
        self.assertEqual(blocked.status_code, 429)
