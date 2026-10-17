"""Canonical JWKS / RS256 verifier vectors for drive (PLAT-988 / PLAT-675).

Mirrors the canonical suite first shipped on pulse (shizuha-tasks
test_jwks_canonical_verifier.py). Asserts the hardened
``ShizuhaJWTAuthentication.get_validated_token`` contract:

  1. Valid HS256 token accepted          (zero regression / dual-accept)
  2. ``alg=none`` rejected
  3. Unsupported alg (RS384) rejected     (alg-allowlist)
  4. HS256/RS256 key-confusion rejected   (RS256 payload HS256-signed w/ pubkey)
  5. Valid RS256 token (kid → JWKS) accepted
  6. Fail-closed: kid-less RS256, unknown-kid, and JWKS-down all rejected

Network is never touched — the JWKS cache is seeded directly.
"""
import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import AccessToken

from drive import authentication as auth

KID = "test-rs256-kid"


def _make_rsa():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


@pytest.fixture
def rsa_keys():
    return _make_rsa()


@pytest.fixture
def seed_jwks(rsa_keys):
    _, pub_pem = rsa_keys
    pubkey = jwt.algorithms.RSAAlgorithm(
        jwt.algorithms.RSAAlgorithm.SHA256
    ).prepare_key(pub_pem.decode())
    auth._JWKS_CACHE["keys"] = {KID: pubkey}
    auth._JWKS_CACHE["fetched_at"] = time.time()
    yield
    auth._JWKS_CACHE["keys"] = {}
    auth._JWKS_CACHE["fetched_at"] = 0


def _rs256(priv_pem, *, kid=KID, alg="RS256", exp_in=3600):
    headers = {} if kid is None else {"kid": kid}
    payload = {"sub": "1", "user_id": 1, "exp": int(time.time()) + exp_in}
    return jwt.encode(payload, priv_pem, algorithm=alg, headers=headers)


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


@pytest.mark.django_db
def test_valid_hs256_accepted():
    raw = str(AccessToken())
    tok = auth.ShizuhaJWTAuthentication().get_validated_token(raw)
    assert tok is not None


def test_alg_none_rejected():
    raw = jwt.encode({"sub": "1"}, key=None, algorithm="none")
    with pytest.raises(InvalidToken):
        auth.ShizuhaJWTAuthentication().get_validated_token(raw)


def test_unsupported_alg_rejected(rsa_keys):
    priv_pem, _ = rsa_keys
    raw = _rs256(priv_pem, alg="RS384")
    with pytest.raises(InvalidToken):
        auth.ShizuhaJWTAuthentication().get_validated_token(raw)


def test_key_confusion_rejected(rsa_keys, seed_jwks):
    """alg=HS256 header, HMAC-signed with the RSA public-key bytes. Strict routing
    sends it to the HS256 path (shared SIGNING_KEY), so the forged signature fails."""
    _, pub_pem = rsa_keys
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    body = _b64(json.dumps({"sub": "1", "user_id": 1, "exp": int(time.time()) + 3600}).encode())
    sig = _b64(hmac.new(pub_pem, head + b"." + body, hashlib.sha256).digest())
    forged = (head + b"." + body + b"." + sig).decode()
    with pytest.raises(InvalidToken):
        auth.ShizuhaJWTAuthentication().get_validated_token(forged)


def test_valid_rs256_accepted(rsa_keys, seed_jwks):
    priv_pem, _ = rsa_keys
    raw = _rs256(priv_pem)
    tok = auth.ShizuhaJWTAuthentication().get_validated_token(raw)
    assert tok is not None
    assert tok.get("user_id") == 1


def test_rs256_missing_kid_rejected(rsa_keys, seed_jwks):
    priv_pem, _ = rsa_keys
    raw = _rs256(priv_pem, kid=None)
    with pytest.raises(InvalidToken):
        auth.ShizuhaJWTAuthentication().get_validated_token(raw)


def test_rs256_unknown_kid_failclosed(rsa_keys, seed_jwks, monkeypatch):
    priv_pem, _ = rsa_keys
    monkeypatch.setattr(auth, "_jwks_fetch_keys", lambda force_refresh=False: {})
    raw = _rs256(priv_pem, kid="some-other-kid")
    with pytest.raises(InvalidToken):
        auth.ShizuhaJWTAuthentication().get_validated_token(raw)
