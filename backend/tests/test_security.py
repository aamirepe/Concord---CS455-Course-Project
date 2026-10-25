"""JWT compatibility and rejection checks for the authentication library."""
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import create_access_token, decode_token


@pytest.mark.parametrize("subject", [42, "42"])
def test_access_token_retains_subject_and_expiry(subject):
    payload = decode_token(create_access_token(subject))
    assert payload["sub"] == "42"
    assert payload["exp"] - payload["iat"] == get_settings().jwt_expire_minutes * 60
    assert abs(payload["iat"] - datetime.now(UTC).timestamp()) < 5


def test_existing_hs256_tokens_remain_valid():
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")

    message = b".".join([
        segment({"alg": "HS256", "typ": "JWT"}),
        segment({"sub": "42", "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp())}),
    ])
    signature = hmac.new(get_settings().secret_key.encode(), message, hashlib.sha256).digest()
    token = message + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
    assert decode_token(token.decode())["sub"] == "42"


def test_expired_token_is_rejected():
    token = create_access_token(42, {"exp": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())})
    with pytest.raises(ValueError, match="Invalid token"):
        decode_token(token)


@pytest.mark.parametrize("algorithm", ["none", "HS512"])
def test_token_algorithm_cannot_override_the_configured_algorithm(algorithm):
    key = "" if algorithm == "none" else get_settings().secret_key
    token = jwt.encode({"sub": "42"}, key, algorithm=algorithm)
    with pytest.raises(ValueError, match="Invalid token"):
        decode_token(token)


def test_token_signed_with_another_key_is_rejected():
    token = jwt.encode({"sub": "42"}, "another-signing-key-at-least-32-bytes", algorithm="HS256")
    with pytest.raises(ValueError, match="Invalid token"):
        decode_token(token)


def test_malformed_token_is_rejected():
    with pytest.raises(ValueError, match="Invalid token"):
        decode_token("not.a.valid-token")
