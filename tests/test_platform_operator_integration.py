"""Local cross-repository HTTP-adapter integration test, enabled explicitly by env."""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

PLATFORM_ROOT = os.environ.get("PER002_PLATFORM_ROOT")
if not PLATFORM_ROOT:
    pytest.skip(
        "Set PER002_PLATFORM_ROOT to run the local platform integration", allow_module_level=True
    )

platform = Path(PLATFORM_ROOT).resolve(strict=True)
sys.path.insert(0, str(platform / "services/person-service/src"))
sys.path.insert(0, str(platform / "shared/python"))

from mes_common import security as platform_security  # noqa: E402
from mes_person_service import operator  # noqa: E402
from mes_person_service.main import app as platform_app  # noqa: E402

from registro_personas.api import create_app  # noqa: E402
from registro_personas.db import get_session  # noqa: E402
from registro_personas.models import AuditRecord  # noqa: E402
from registro_personas.security import _verifier  # noqa: E402

ISSUER = "https://identity.example.invalid/realms/per002-test"
AUDIENCE = "mes-api"
DELEGATION_ISSUER = "https://platform.example.invalid/persons-delegation"
DELEGATION_AUDIENCE = "registro-personas"
ROLE_SET = [
    "persons.read",
    "persons.register",
    "persons.review",
    "persons.link",
    "persons.source.demo",
]


def _signed_token(
    private_key, *, roles=ROLE_SET, audience=AUDIENCE, subject="synthetic-per002-operator"
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": audience,
            "sub": subject,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "realm_access": {"roles": roles},
        },
        private_key,
        algorithm="RS256",
    )


def test_operator_register_query_conflict_and_audit(
    db_engine, tmp_path, monkeypatch, caplog
) -> None:
    operator_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    delegation_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_path = tmp_path / "per002-test-public.pem"
    public_key_path.write_bytes(
        delegation_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    delegation_private_path = tmp_path / "per002-test-private.pem"
    delegation_private_path.write_bytes(
        delegation_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("REGISTRY_DELEGATION_ISSUER", DELEGATION_ISSUER)
    monkeypatch.setenv("REGISTRY_DELEGATION_AUDIENCE", DELEGATION_AUDIENCE)
    monkeypatch.setenv("REGISTRY_DELEGATION_PUBLIC_KEY_FILE", str(public_key_path))
    monkeypatch.setenv("REGISTRY_DELEGATION_KEY_ID", "integration-test")
    monkeypatch.delenv("REGISTRY_DELEGATION_JWKS_URL", raising=False)
    monkeypatch.setenv("PERSONS_DELEGATION_ISSUER", DELEGATION_ISSUER)
    monkeypatch.setenv("PERSONS_DELEGATION_AUDIENCE", DELEGATION_AUDIENCE)
    monkeypatch.setenv("PERSONS_DELEGATION_KEY_ID", "integration-test")
    monkeypatch.setenv("PERSONS_DELEGATION_PRIVATE_KEY_FILE", str(delegation_private_path))
    monkeypatch.setenv("PERSONS_DELEGATION_TTL_SECONDS", "60")
    policy_path = tmp_path / "persons-authorization.json"
    policy_path.write_text(
        json.dumps(
            {
                "operators": [
                    {
                        "issuer": ISSUER,
                        "subject": "synthetic-per002-operator",
                        "permissions": [
                            "persons.read",
                            "persons.register",
                            "persons.review",
                            "persons.link",
                        ],
                        "sources": ["demo"],
                    },
                    {
                        "issuer": ISSUER,
                        "subject": "no-source-operator",
                        "permissions": ["persons.read", "persons.register"],
                        "sources": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PERSONS_AUTHORIZATION_POLICY_FILE", str(policy_path))
    operator._settings.persons_oidc_issuer_url = ISSUER
    operator._settings.persons_oidc_client_id = "interoperabilidad-admin-lab"
    operator._settings.persons_oidc_redirect_uri = "http://127.0.0.1:8003/admin/personas"
    operator._settings.oidc_issuer_url = ISSUER
    from mes_person_service.delegation import _clear_signing_config

    _clear_signing_config()
    _verifier.cache_clear()
    monkeypatch.setattr(platform_security, "_issuer", ISSUER)
    monkeypatch.setattr(platform_security, "_pinned_public_key", operator_key.public_key())
    monkeypatch.setattr(platform_security, "_issuer", ISSUER)
    monkeypatch.setattr(platform_security._settings, "oidc_audience", AUDIENCE)
    monkeypatch.setenv("REGISTRY_PERSONS_URL", "http://127.0.0.1:8013")

    persons_app = create_app()

    def session():
        with Session(db_engine, expire_on_commit=False) as value:
            yield value

    persons_app.dependency_overrides[get_session] = session
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        operator.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.ASGITransport(app=persons_app), **kwargs
        ),
    )
    token = _signed_token(operator_key)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())}
    source = {"system": "demo", "entity": "fixture", "external_id": "SYNTHETIC-PER002-001"}
    payload = {
        "display_name": "Persona Ficticia PER-002",
        "source": source,
        "provenance": {
            "system": "demo",
            "evidence_ref": str(uuid4()),
            "observed_at": "2026-11-20T12:00:00Z",
        },
    }

    try:
        with TestClient(platform_app) as client:
            unauthenticated = client.get("/api/v1/personas")
            assert unauthenticated.status_code == 401

            no_source_role = _signed_token(
                operator_key,
                roles=ROLE_SET,
                subject="no-source-operator",
            )
            denied = client.post(
                "/api/v1/personas",
                headers={
                    "Authorization": f"Bearer {no_source_role}",
                    **{"Idempotency-Key": str(uuid4())},
                },
                json=payload,
            )
            assert denied.status_code == 403

            created = client.post("/api/v1/personas", headers=headers, json=payload)
            assert created.status_code == 201
            person = created.json()
            assert person["display_name"] == payload["display_name"]
            assert (
                client.get(f"/api/v1/personas/{person['person_id']}", headers=headers).json()
                == person
            )
            resolved = client.get(
                "/api/v1/personas/referencias/resolver",
                params=source,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert (
                resolved.status_code == 200 and resolved.json()["person_id"] == person["person_id"]
            )
            references = client.get(
                f"/api/v1/personas/{person['person_id']}/referencias",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert references.status_code == 200
            assert references.json()[0]["source_record_id"] == source["external_id"]

            additional_source = {
                "system": "demo",
                "entity": "fixture",
                "external_id": "SYNTHETIC-PER002-SECOND-SOURCE",
            }
            link_data = {
                "source": additional_source,
                "provenance": {
                    "system": "demo",
                    "evidence_ref": str(uuid4()),
                    "observed_at": "2026-11-20T12:00:00Z",
                },
            }
            link_path = f"/api/v1/personas/{person['person_id']}/referencias"
            linked = client.post(
                link_path, headers={"Authorization": f"Bearer {token}"}, json=link_data
            )
            assert linked.status_code == 201
            assert linked.json()["source_record_id"] == additional_source["external_id"]
            replayed = client.post(
                link_path, headers={"Authorization": f"Bearer {token}"}, json=link_data
            )
            assert replayed.status_code == 200
            linked_refs = client.get(link_path, headers={"Authorization": f"Bearer {token}"})
            assert linked_refs.status_code == 200 and len(linked_refs.json()) == 2

            second_payload = {
                **payload,
                "display_name": "Otra Persona Ficticia PER-002",
                "source": {
                    "system": "demo",
                    "entity": "fixture",
                    "external_id": "SYNTHETIC-PER002-OTHER",
                },
            }
            second = client.post(
                "/api/v1/personas",
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
                json=second_payload,
            )
            assert second.status_code == 201
            reassignment = client.post(
                f"/api/v1/personas/{second.json()['person_id']}/referencias",
                headers={"Authorization": f"Bearer {token}"},
                json=link_data,
            )
            assert reassignment.status_code == 409
            assert second.json()["person_id"] not in reassignment.text
            assert (
                len(
                    client.get(
                        f"/api/v1/personas/{second.json()['person_id']}/referencias",
                        headers={"Authorization": f"Bearer {token}"},
                    ).json()
                )
                == 1
            )

            duplicate = client.post(
                "/api/v1/personas",
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
                json=payload,
            )
            assert duplicate.status_code == 409
            assert source["external_id"] not in duplicate.text
            reviews = client.get(
                "/api/v1/personas/revisiones", headers={"Authorization": f"Bearer {token}"}
            )
            assert reviews.status_code == 200 and reviews.json()["total"] == 2
            assert reviews.json()["items"][0]["kind"] == "source_reference_collision"

            bad_audience = _signed_token(operator_key, audience="other-api")
            invalid = client.get(
                "/api/v1/personas",
                headers={"Authorization": f"Bearer {bad_audience}"},
            )
            assert invalid.status_code == 401

        with Session(db_engine) as db:
            records = db.query(AuditRecord).all()
            assert len(records) >= 6
            assert {record.actor_issuer for record in records} == {ISSUER}
            assert {record.actor_subject for record in records} == {"synthetic-per002-operator"}
    finally:
        _verifier.cache_clear()
