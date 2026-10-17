"""
Federated JWT Authentication for Shizuha Drive

This module provides JWT authentication that works with Shizuha ID tokens.
Users don't need to exist in the local database - we use FederatedUser objects
for users that exist only in Shizuha ID.
"""
import logging
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User

from drive.token_revocation import is_token_revoked

logger = logging.getLogger(__name__)


class FederatedUser:
    """
    A lightweight user object for users authenticated via Shizuha ID
    but not present in the local database.

    This allows the service to work with federated authentication without
    requiring user records to be replicated across all services.
    """

    def __init__(
        self,
        user_id,
        email=None,
        username=None,
        enabled_services=None,
        organization_id=None,
        organization_ids=None,
        organization_memberships=None,
    ):
        self.id = user_id
        self.pk = user_id
        self.email = email or ''
        self.username = username or email or str(user_id)
        self.is_authenticated = True
        self.is_active = True
        self.is_staff = False
        self.is_superuser = False
        # None = unrestricted (staff/superuser), [] = no services, [...] = specific services
        self.enabled_services = enabled_services

        # DRV-67: preserve trusted org claims from Shizuha ID tokens.  Drive's
        # share create/read gates derive owner/grantee org boundaries from these
        # user attributes; dropping them made live federated users look org-less.
        memberships = organization_memberships if isinstance(organization_memberships, dict) else {}
        orgs = []
        if organization_ids:
            if isinstance(organization_ids, str):
                orgs.append(organization_ids)
            else:
                orgs.extend(str(v) for v in organization_ids if v not in (None, ''))
        orgs.extend(str(k) for k in memberships.keys() if k not in (None, ''))
        if organization_id in (None, '') and len(set(orgs)) == 1:
            organization_id = orgs[0]
        self.organization_id = str(organization_id) if organization_id not in (None, '') else None
        self.organization_ids = sorted(set(orgs))
        self.org_ids = self.organization_ids
        self.organization_memberships = memberships

    def __str__(self):
        return self.username or str(self.id)

    def save(self, *args, **kwargs):
        """No-op save for compatibility"""
        pass

    @property
    def is_anonymous(self):
        return False


# ───────────────────────── OAuth 2.0 advisory + enforcement ─────────────────
# Inlined from packages/shizuha-auth: Phase 2/4 of OAuth migration. Services
# that import the shared package should prefer that copy; this duplicate
# keeps parity while the migration to the shared package is in-flight.

def _oauth_advisory_claim_check(validated_token):
    """Log mismatched `aud`/`iss` claims without rejecting. Returns status dict."""
    import os as _os
    from django.conf import settings as _st
    service_name = getattr(_st, 'SHIZUHA_SERVICE_NAME', '') or _os.environ.get('SHIZUHA_SERVICE_NAME', '')
    expected_issuer = getattr(_st, 'SHIZUHA_EXPECTED_ISSUER', '') or _os.environ.get('SHIZUHA_EXPECTED_ISSUER', '')
    aud = validated_token.get('aud')
    if aud is not None and aud not in ('*', ['*']):
        aud_list = aud if isinstance(aud, list) else [aud]
        if service_name and service_name not in aud_list:
            logger.warning(
                f"JWT aud={aud_list!r} does not include service={service_name!r}. "
                f"Phase 2 advisory — token accepted."
            )
    iss = validated_token.get('iss')
    if iss is not None and expected_issuer and iss != expected_issuer:
        logger.warning(
            f"JWT iss={iss!r} does not match expected={expected_issuer!r}. "
            f"Phase 2 advisory — token accepted."
        )


def _oauth_enforce_audience_claim(validated_token):
    """Enforce the Drive-resource audience for the Drive Desktop client.

    DRV-106 audience contract (DRV-104 HLD v2.5 §7): a Drive Desktop access
    token is accepted only for the frozen Drive audience; a client-id-only or
    wrong-audience token is rejected. Enforcement is ALWAYS on for the Drive
    Desktop client (client_id == 'drive-desktop'), independent of the global
    SHIZUHA_AUD_ENFORCE flag, so the desktop profile fails closed even if the
    global Phase-4 flag is not yet flipped. Non-desktop tokens keep the
    advisory/flag-gated behavior (their `aud` is the scope list, not the
    resource audience).
    """
    import os as _os
    from django.conf import settings as _st
    from rest_framework_simplejwt.exceptions import InvalidToken as _InvalidToken
    enforce = bool(getattr(_st, 'SHIZUHA_AUD_ENFORCE', False)) or \
        _os.environ.get('SHIZUHA_AUD_ENFORCE', '').lower() in ('1', 'true', 'yes')
    is_drive_desktop = (validated_token.get('client_id') or '') == 'drive-desktop'
    if not (enforce or is_drive_desktop):
        return
    service_name = getattr(_st, 'SHIZUHA_SERVICE_NAME', '') or _os.environ.get('SHIZUHA_SERVICE_NAME', '')
    if not service_name:
        return
    aud = validated_token.get('aud')
    if aud is None or aud == '*' or aud == ['*']:
        return
    aud_list = aud if isinstance(aud, list) else [aud]
    if service_name not in aud_list:
        logger.warning(
            f"JWT rejected: aud={aud_list!r} does not include required service={service_name!r}"
        )
        raise _InvalidToken(f"Token audience {aud_list} does not include '{service_name}'")


def _raw_token_to_str(raw_token):
    if isinstance(raw_token, bytes):
        try:
            return raw_token.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise InvalidToken('Token is not valid UTF-8') from exc
    return str(raw_token)


def _oauth_enforce_token_not_revoked(validated_token, raw_token):
    """Reject any token revoked by shizuha-id.

    The shared client checks OAuth and session tokens and fails closed
    on lookup errors, so every authentication path calls this after local
    signature/expiry validation.
    """
    if is_token_revoked(validated_token, _raw_token_to_str(raw_token)):
        raise InvalidToken('Token has been revoked')


# ───────────────────────── OAuth 2.0 RS256 validation ──────────────────────
# Phase 5 of OAuth migration: accept tokens signed with RS256 (shizuha-id's
# RSA key, public half published at /id/.well-known/jwks.json). Falls back
# from default HS256 validation so legacy tokens keep working during grace.

class _Rs256Token(dict):
    """Lightweight dict-like token — satisfies simplejwt get_user() interface."""
    pass


_JWKS_CACHE = {'keys': {}, 'fetched_at': 0}
_JWKS_TTL = 600  # seconds
_ALLOWED_JWT_ALGS = {'HS256', 'RS256'}


def _jwks_fetch_keys(force_refresh=False):
    """Fetch/cache shizuha-id JWKS keys by kid (canonical, PLAT-675 — honor kid, fail closed).

    A forced refresh (used on kid miss/rotation) returns ONLY the just-fetched key set,
    never stale keys, so an unknown kid or JWKS outage cannot validate against an old key.
    """
    import json as _json, os as _os, time as _time, urllib.request as _urlreq
    import jwt as _jwt
    if (not force_refresh and _time.time() - _JWKS_CACHE['fetched_at'] < _JWKS_TTL
            and _JWKS_CACHE['keys']):
        return _JWKS_CACHE['keys']
    issuer = (_os.environ.get('SHIZUHA_EXPECTED_ISSUER') or
              'http://shizuha-id:8001').rstrip('/')
    jwks_url = _os.environ.get('SHIZUHA_OAUTH_JWKS_URL') or f'{issuer}/.well-known/jwks.json'
    try:
        resp = _urlreq.urlopen(jwks_url, timeout=5)
        jwks = _json.loads(resp.read())
        keys = {
            k['kid']: _jwt.algorithms.RSAAlgorithm.from_jwk(_json.dumps(k))
            for k in jwks.get('keys', [])
            if k.get('kid') and k.get('kty') == 'RSA' and k.get('alg') in (None, 'RS256')
        }
        _JWKS_CACHE['keys'] = keys
        _JWKS_CACHE['fetched_at'] = _time.time()
        return keys
    except Exception as _exc:
        logger.warning(f'JWKS fetch from {jwks_url} failed: {_exc}')
        return {} if force_refresh else _JWKS_CACHE['keys']


def _try_rs256_validate(raw_token):
    """Return a _Rs256Token on success, None if not RS256, raise if bad RS256.

    Canonical (PLAT-675/PLAT-555): alg-allowlist, REQUIRE kid, fail closed on a
    missing/unknown key — never fall back to an arbitrary JWKS key for a kid-less token.
    """
    import jwt as _jwt
    from rest_framework_simplejwt.exceptions import InvalidToken as _InvalidToken
    try:
        header = _jwt.get_unverified_header(raw_token)
    except Exception:
        return None
    alg = header.get('alg')
    if alg not in _ALLOWED_JWT_ALGS:
        raise _InvalidToken(f'Unsupported JWT alg={alg!r}')
    if alg != 'RS256':
        return None
    kid = header.get('kid')
    if not kid:
        raise _InvalidToken('RS256 token missing kid')
    keys = _jwks_fetch_keys()
    key = keys.get(kid)
    if key is None:
        # Re-fetch once in case of key rotation; fail closed on miss/outage.
        keys = _jwks_fetch_keys(force_refresh=True)
        key = keys.get(kid)
    if key is None:
        raise _InvalidToken(f'No JWKS key available for kid={kid!r}')
    try:
        payload = _jwt.decode(
            raw_token, key=key, algorithms=['RS256'],
            options={'verify_aud': False, 'verify_exp': True},
        )
    except _jwt.PyJWTError as exc:
        raise _InvalidToken(f'RS256 validation failed: {exc}')
    return _Rs256Token(payload)

class ShizuhaJWTAuthentication(JWTAuthentication):
    """
    Custom JWT authentication that works with Shizuha ID tokens.

    If the user exists locally, returns the Django User object.
    If the user only exists in Shizuha ID, returns a FederatedUser object.

    This allows all Shizuha platform services to authenticate users
    without requiring user synchronization.
    """


    def get_validated_token(self, raw_token):
        """CANONICAL JWKS verifier — keep in sync, do not fork (RS256 migration, PLAT-675).

        alg-allowlist {HS256, RS256} only (rejects alg='none'/others up front); route
        STRICTLY by the declared alg and validate with ONLY the matching key (no
        HS256/RS256 key-confusion). HS256 = shared secret (legacy/default); RS256 =
        shizuha-id JWKS, fail-closed. HS256 stays accepted for dual-accept rollback.
        """
        import jwt as _jwt
        from rest_framework_simplejwt.exceptions import InvalidToken as _InvalidToken
        try:
            header = _jwt.get_unverified_header(raw_token) or {}
        except Exception:
            raise _InvalidToken('Malformed JWT header')
        alg = (header.get('alg') or '').upper()
        if alg not in _ALLOWED_JWT_ALGS:
            raise _InvalidToken(f'Unsupported JWT alg {alg!r}: only HS256/RS256 accepted')
        if alg == 'RS256':
            tok = _try_rs256_validate(raw_token)
            if tok is None:
                raise _InvalidToken('RS256 token failed JWKS validation')
            _oauth_enforce_token_not_revoked(tok, raw_token)
            return tok
        tok = super().get_validated_token(raw_token)
        _oauth_enforce_token_not_revoked(tok, raw_token)
        return tok

    def get_user(self, validated_token):
        """
        Get or create a user from the validated JWT token.

        The token contains:
        - user_id: UUID of the user in Shizuha ID
        - email: User's email address
        - username: User's username (optional)
        """
        _oauth_advisory_claim_check(validated_token)
        _oauth_enforce_audience_claim(validated_token)
        try:
            user_id = validated_token.get('user_id')
            email = validated_token.get('email', '')
            username = validated_token.get('username', email)

            if not user_id:
                logger.warning("JWT token missing user_id claim")
                raise InvalidToken("Token missing user_id claim")

            # Extract enabled_services from token
            enabled_services = validated_token.get('enabled_services')
            # Staff/superuser override
            if validated_token.get('is_staff') or validated_token.get('is_superuser'):
                enabled_services = None  # None = unrestricted

            # CRITICAL: trust the token's email claim when present (SSO
            # happy path). Local-DB lookups by username/email would hit
            # the local admin seed (admin@example.com at id=1) for tokens
            # whose user_id happens to equal the local PK.
            if email:
                memberships = validated_token.get('organization_memberships') or {}
                organization_ids = (
                    validated_token.get('organization_ids')
                    or validated_token.get('org_ids')
                    or list(memberships.keys())
                )
                organization_id = (
                    validated_token.get('organization_id')
                    or validated_token.get('org_id')
                    or validated_token.get('organizationId')
                )
                return FederatedUser(
                    user_id=user_id,
                    email=email,
                    username=username,
                    enabled_services=enabled_services,
                    organization_id=organization_id,
                    organization_ids=organization_ids,
                    organization_memberships=memberships,
                )

            # Legacy / no-email token — try local user, then placeholder.
            try:
                user = User.objects.get(username=str(user_id))
                return user
            except User.DoesNotExist:
                pass

            logger.debug(
                f"FederatedUser placeholder for {user_id} (no email claim)"
            )
            return FederatedUser(
                user_id=user_id,
                email=email,
                username=username,
                enabled_services=enabled_services,
            )

        except TokenError as e:
            logger.error(f"Token error: {e}")
            raise InvalidToken(str(e))
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            raise InvalidToken(f"Authentication failed: {e}")
