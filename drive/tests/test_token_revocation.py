"""Revocation enforcement tests for PLAT-1035."""
import time
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.authentication import JWTAuthentication

from drive import authentication as auth
from drive.token_revocation import (
    _CACHE_TTL_SECONDS,
    _REVOCATION_CACHE,
    _cache_key,
    is_token_revoked,
)

KID = "revocation-test-kid"


@pytest.fixture(autouse=True)
def clear_revocation_cache():
    _REVOCATION_CACHE.clear()
    yield
    _REVOCATION_CACHE.clear()


@pytest.fixture
def rsa_keys():
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


def _hs256_raw():
    # Structurally valid HS256 JWT so Drive's alg allowlist routes to the HS path;
    # JWTAuthentication.get_validated_token is monkeypatched in the HS tests below.
    return jwt.encode({"sub": "1"}, "test-secret-with-at-least-32-bytes", algorithm="HS256")


def _rs256_raw(priv_pem, *, client_id="drive"):
    payload = {
        "sub": "1",
        "user_id": 1,
        "client_id": client_id,
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": KID})


@pytest.mark.parametrize("claims", [{}, {"client_id": ""}])
def test_session_without_client_id_is_checked(claims, monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"active": False}
    post = MagicMock(return_value=resp)
    monkeypatch.setattr("drive.token_revocation.requests.post", post)
    assert is_token_revoked(claims, "raw.token.value") is True
    post.assert_called_once()


def test_revoked_token_returns_true(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"active": False}
    monkeypatch.setattr("drive.token_revocation.requests.post", MagicMock(return_value=resp))

    assert is_token_revoked({"client_id": "drive"}, "revoked.jwt") is True


def test_active_token_returns_false(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"active": True}
    monkeypatch.setattr("drive.token_revocation.requests.post", MagicMock(return_value=resp))

    assert is_token_revoked({"client_id": "drive"}, "active.jwt") is False


def test_network_error_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "drive.token_revocation.requests.post",
        MagicMock(side_effect=OSError("connection refused")),
    )

    assert is_token_revoked({"client_id": "drive"}, "some.jwt") is True


def test_non_200_response_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "drive.token_revocation.requests.post",
        MagicMock(return_value=MagicMock(status_code=503)),
    )

    assert is_token_revoked({"client_id": "drive"}, "some.jwt") is True


def test_bad_json_response_fails_closed(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.side_effect = ValueError("bad json")
    monkeypatch.setattr("drive.token_revocation.requests.post", MagicMock(return_value=resp))

    assert is_token_revoked({"client_id": "drive"}, "some.jwt") is True


def test_cache_hit_prevents_duplicate_lookup(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"active": True}
    post = MagicMock(return_value=resp)
    monkeypatch.setattr("drive.token_revocation.requests.post", post)

    is_token_revoked({"client_id": "drive"}, "cached.jwt")
    is_token_revoked({"client_id": "drive"}, "cached.jwt")

    post.assert_called_once()


def test_expired_cache_triggers_new_lookup(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"active": True}
    post = MagicMock(return_value=resp)
    monkeypatch.setattr("drive.token_revocation.requests.post", post)
    token = "expiry.jwt"
    _REVOCATION_CACHE[_cache_key(token)] = (
        time.monotonic() - _CACHE_TTL_SECONDS - 1,
        False,
    )

    is_token_revoked({"client_id": "drive"}, token)

    post.assert_called_once()


def test_hs256_path_enforces_revocation_check(monkeypatch):
    raw = _hs256_raw()
    token = {"user_id": 1, "client_id": "drive"}
    revoked = MagicMock(return_value=False)
    monkeypatch.setattr(JWTAuthentication, "get_validated_token", MagicMock(return_value=token))
    monkeypatch.setattr(auth, "is_token_revoked", revoked)

    validated = auth.ShizuhaJWTAuthentication().get_validated_token(raw)

    assert validated is token
    revoked.assert_called_once_with(validated, raw)


def test_hs256_revoked_token_is_rejected(monkeypatch):
    monkeypatch.setattr(
        JWTAuthentication,
        "get_validated_token",
        MagicMock(return_value={"user_id": 1, "client_id": "drive"}),
    )
    monkeypatch.setattr(auth, "is_token_revoked", MagicMock(return_value=True))

    with pytest.raises(InvalidToken):
        auth.ShizuhaJWTAuthentication().get_validated_token(_hs256_raw())


def test_rs256_path_enforces_revocation_check(rsa_keys, seed_jwks, monkeypatch):
    priv_pem, _ = rsa_keys
    raw = _rs256_raw(priv_pem)
    revoked = MagicMock(return_value=False)
    monkeypatch.setattr(auth, "is_token_revoked", revoked)

    validated = auth.ShizuhaJWTAuthentication().get_validated_token(raw)

    assert validated.get("client_id") == "drive"
    revoked.assert_called_once_with(validated, raw)


def test_rs256_revoked_token_is_rejected(rsa_keys, seed_jwks, monkeypatch):
    priv_pem, _ = rsa_keys
    monkeypatch.setattr(auth, "is_token_revoked", MagicMock(return_value=True))

    with pytest.raises(InvalidToken):
        auth.ShizuhaJWTAuthentication().get_validated_token(_rs256_raw(priv_pem))
