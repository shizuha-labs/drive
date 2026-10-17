"""DRV-22 — free-validation waitlist: public submit (idempotent), staff inspect/export."""
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import WaitlistEntry
from drive.views import WaitlistView


class _U:
    def __init__(self, uid='u1', is_staff=False):
        self.id = uid
        self.pk = uid
        self.is_staff = is_staff
        self.is_authenticated = True


class DRV22WaitlistSubmitTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def _post(self, **data):
        req = self.factory.post('/api/drive/waitlist/', data, format='json')
        return WaitlistView.as_view()(req)

    def test_public_submit_creates_entry(self):
        resp = self._post(email='Alice@Example.com', name='Alice', interest='photo backup')
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['ok'])
        self.assertFalse(resp.data['already_on_waitlist'])
        e = WaitlistEntry.objects.get()
        self.assertEqual(e.email, 'alice@example.com')  # stored lower-cased
        self.assertEqual(e.name, 'Alice')
        self.assertEqual(e.status, WaitlistEntry.STATUS_PENDING)
        self.assertEqual(e.source, 'drive_landing')

    def test_resubmit_is_idempotent_no_duplicate(self):
        self._post(email='bob@example.com', name='Bob')
        resp = self._post(email='BOB@example.com', name='Bobby', interest='team files')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['already_on_waitlist'])
        self.assertEqual(WaitlistEntry.objects.count(), 1)  # case-insensitive dedupe
        e = WaitlistEntry.objects.get()
        self.assertEqual(e.name, 'Bobby')           # update_or_create updated it
        self.assertEqual(e.interest, 'team files')

    def test_invalid_email_rejected(self):
        for bad in ('', 'notanemail', 'foo@bar'):
            self.assertEqual(self._post(email=bad).status_code, 400)
        self.assertEqual(WaitlistEntry.objects.count(), 0)


class DRV22WaitlistInspectTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        WaitlistEntry.objects.create(email='a@example.com', name='A', source='drive_landing')
        WaitlistEntry.objects.create(email='b@example.com', name='B', status=WaitlistEntry.STATUS_INVITED)

    def _get(self, user=None, **params):
        req = self.factory.get('/api/drive/waitlist/', params)
        if user is not None:
            force_authenticate(req, user=user)
        return WaitlistView.as_view()(req)

    def test_anonymous_and_non_staff_forbidden(self):
        self.assertEqual(self._get().status_code, 403)
        self.assertEqual(self._get(_U(is_staff=False)).status_code, 403)

    def test_staff_can_list(self):
        resp = self._get(_U(is_staff=True))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 2)

    def test_staff_status_filter(self):
        resp = self._get(_U(is_staff=True), status='invited')
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['email'], 'b@example.com')

    def test_staff_csv_export(self):
        resp = self._get(_U(is_staff=True), export="csv")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv')
        body = resp.content.decode()
        self.assertIn('email,name,interest,source,status,created_at', body)
        self.assertIn('a@example.com', body)
        self.assertIn('b@example.com', body)


class DRV22WaitlistEmailTests(TestCase):
    """VEN-150: confirmation email sent on successful signup; failure does not block."""

    def setUp(self):
        self.factory = APIRequestFactory()

    def _post(self, **data):
        req = self.factory.post('/api/drive/waitlist/', data, format='json')
        return WaitlistView.as_view()(req)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_confirmation_email_sent_on_signup(self):
        """Successful signup sends a confirmation email."""
        resp = self._post(email='alice@example.com', name='Alice', interest='photo backup')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn("You're on the Shizuha Drive waitlist!", msg.subject)
        self.assertIn('alice@example.com', msg.to)
        self.assertIn('Hi Alice', msg.body)
        self.assertIn('alice@example.com', msg.body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_confirmation_email_on_resubmit(self):
        """Re-submitting also sends a confirmation (update_or_create path)."""
        self._post(email='bob@example.com', name='Bob')
        mail.outbox.clear()
        resp = self._post(email='bob@example.com', name='Bobby')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['already_on_waitlist'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Bobby', mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_email_failure_does_not_block_response(self):
        """Email backend failure is logged but does not block the 201 response."""
        with patch('django.core.mail.send_mail', side_effect=Exception("SMTP down")):
            resp = self._post(email='carol@example.com', name='Carol')
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['ok'])
        # Entry is still created despite email failure
        self.assertEqual(WaitlistEntry.objects.filter(email='carol@example.com').count(), 1)
