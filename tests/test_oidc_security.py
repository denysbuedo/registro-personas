from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from registro_personas.api import create_app
from registro_personas.security import _verifier

ISSUER = "https://platform.example.invalid/delegation"
AUDIENCE = "registro-personas"


@pytest.fixture
def signer(tmp_path, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_path = tmp_path / "test-public.pem"
    # PEM is required by PyJWT; serialize the public key in the standard format.
    from cryptography.hazmat.primitives import serialization

    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("REGISTRY_DELEGATION_ISSUER", ISSUER)
    monkeypatch.setenv("REGISTRY_DELEGATION_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("REGISTRY_DELEGATION_PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.setenv("REGISTRY_DELEGATION_KEY_ID", "registry-test-key")
    monkeypatch.delenv("REGISTRY_DELEGATION_JWKS_URL", raising=False)
    _verifier.cache_clear()
    yield private_key
    _verifier.cache_clear()


def token(signer, *, permissions=None, sources=None, **overrides):
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "synthetic-operator-subject",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "actor_iss": "https://identity.example.invalid/realms/per002",
        "token_use": "persons_delegation_v1",
        "permissions": permissions or [],
        "sources": sources or [],
        "jti": "synthetic-jti-001",
    }
    claims.update(overrides)
    return jwt.encode(claims, signer, algorithm="RS256", headers={"kid": "registry-test-key"})


def test_delegated_permissions_are_derived_from_verified_signed_claims(signer):
    from fastapi.security import HTTPAuthorizationCredentials

    from registro_personas.security import authenticate

    result = authenticate(
        HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token(
                signer,
                permissions=["persons.read", "persons.list", "persons.create"],
                sources=["reduniv.conecta"],
            ),
        )
    )
    assert result.permissions == frozenset({"persons.read", "persons.list", "persons.create"})
    assert result.source_systems == frozenset({"reduniv.conecta"})
    assert result.actor_id.int != 0
    assert result.issuer == "https://identity.example.invalid/realms/per002"
    assert result.subject == "synthetic-operator-subject"


@pytest.mark.parametrize(
    "mutation",
    [
        {"iss": "https://other.example.invalid/realm"},
        {"aud": "other-api"},
        {"exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())},
        {"iat": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp())},
    ],
)
def test_bad_signature_claims_are_denied_before_database(signer, mutation):
    app = create_app()
    with TestClient(app) as client:
        response = client.get(
            "/v1/personas", headers={"Authorization": f"Bearer {token(signer, **mutation)}"}
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_valid_but_unprivileged_token_is_forbidden_without_database(signer):
    from registro_personas.db import get_session

    app = create_app()

    def no_database():
        yield object()

    app.dependency_overrides[get_session] = no_database
    with TestClient(app) as client:
        response = client.get(
            "/v1/personas",
            headers={"Authorization": f"Bearer {token(signer, permissions=['persons.review'])}"},
        )
    assert response.status_code == 403


def test_no_bearer_token_is_denied(signer):
    with TestClient(create_app()) as client:
        response = client.get("/v1/personas")
    assert response.status_code == 401


def test_original_oidc_bearer_is_not_accepted_as_delegation(signer):
    original = jwt.encode(
        {
            "iss": "https://identity.example.invalid",
            "aud": AUDIENCE,
            "sub": "operator",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(seconds=60)).timestamp()),
        },
        signer,
        algorithm="RS256",
    )
    with TestClient(create_app()) as client:
        response = client.get("/v1/personas", headers={"Authorization": f"Bearer {original}"})
    assert response.status_code == 401


def test_key_id_must_match_the_pinned_delegation_key(signer, monkeypatch):
    monkeypatch.setenv("REGISTRY_DELEGATION_KEY_ID", "different-key")
    _verifier.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get(
            "/v1/personas",
            headers={
                "Authorization": f"Bearer {token(signer, permissions=['persons.read', 'persons.list'])}"
            },
        )
    assert response.status_code == 401
