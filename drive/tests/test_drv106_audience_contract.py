"""DRV-106 audience contract tests — Drive Desktop tokens accepted only for the
Drive-resource audience.

Mirrors the DRV-104 HLD v2.5 §7 audience boundary: a Drive Desktop access token
(client_id == 'drive-desktop') is accepted by Drive only when its `aud` includes
the Drive resource audience; a client-id-only or wrong-audience token is
rejected. Non-desktop tokens keep the flag-gated/advisory behavior (their `aud`
is the scope list, not the resource audience), so existing traffic is not broken.
"""
import pytest
from rest_framework_simplejwt.exceptions import InvalidToken

from drive import authentication as auth


def _token(**claims):
    """A minimal validated-token dict (post-JWKS) with the claims we test."""
    base = {
        'user_id': 1,
        'email': 'drv106@example.com',
        'client_id': 'drive-desktop',
        'aud': ['drive'],
        'scope': 'drive:files.read drive:files.write profile',
    }
    base.update(claims)
    return base


@pytest.mark.django_db
def test_drive_desktop_token_with_drive_audience_accepted():
    # Must not raise.
    auth._oauth_enforce_audience_claim(_token())


@pytest.mark.django_db
def test_drive_desktop_client_id_only_audience_rejected():
    # aud == [client_id] (client-id-only audience) must fail closed.
    with pytest.raises(InvalidToken):
        auth._oauth_enforce_audience_claim(_token(aud=['drive-desktop']))


@pytest.mark.django_db
def test_drive_desktop_wrong_audience_rejected():
    with pytest.raises(InvalidToken):
        auth._oauth_enforce_audience_claim(_token(aud=['other-service']))


@pytest.mark.django_db
def test_drive_desktop_wildcard_audience_accepted():
    # Wildcard aud is treated as "not scoped" — accepted (matches ID behavior).
    auth._oauth_enforce_audience_claim(_token(aud='*'))


@pytest.mark.django_db
def test_non_desktop_client_not_enforced_when_flag_off():
    # A non-desktop OAuth token (scope-list aud) is NOT rejected when the global
    # SHIZUHA_AUD_ENFORCE flag is off — existing traffic keeps working.
    tok = _token(client_id='web-client', aud=['drive:files.read'])
    auth._oauth_enforce_audience_claim(tok)
