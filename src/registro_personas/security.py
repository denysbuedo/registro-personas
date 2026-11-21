"""Validate short-lived, platform-signed delegated authorization assertions."""

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from .errors import DomainError
from .schemas import AccountLink

bearer = HTTPBearer(auto_error=False)
_security_logger = logging.getLogger("registro_personas.security")
_SOURCE_ALIAS = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_ALLOWED_PERMISSIONS = frozenset(
    {
        "persons.read",
        "persons.list",
        "persons.create",
        "persons.review",
        "persons.references.link",
        "accounts.link",
    }
)


@dataclass(frozen=True)
class Principal:
    """Derived exclusively from a signature- and claim-validated access token."""

    actor_id: UUID
    permissions: frozenset[str] = frozenset()
    source_systems: frozenset[str] = frozenset()
    readable_person_ids: frozenset[UUID] = frozenset()
    linkable_person_ids: frozenset[UUID] = frozenset()
    issuer: str | None = None
    subject: str | None = None


class OwnershipVerifier(Protocol):
    def verify(self, principal: Principal, person_id: UUID, link: AccountLink) -> bool: ...


class DenyOwnership:
    def verify(self, principal: Principal, person_id: UUID, link: AccountLink) -> bool:
        return False


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache(maxsize=1)
def _verifier() -> tuple[str, str, str | None, str | None, PyJWKClient | None, str | None]:
    issuer = os.environ.get("REGISTRY_DELEGATION_ISSUER", "").rstrip("/")
    audience = os.environ.get("REGISTRY_DELEGATION_AUDIENCE", "")
    jwks_url = os.environ.get("REGISTRY_DELEGATION_JWKS_URL")
    public_key_file = os.environ.get("REGISTRY_DELEGATION_PUBLIC_KEY_FILE")
    expected_key_id = os.environ.get("REGISTRY_DELEGATION_KEY_ID")
    try:
        issuer_parts = urlsplit(issuer)
        jwks_parts = urlsplit(jwks_url) if jwks_url else None
    except ValueError:
        raise DomainError(
            503, "authentication-unavailable", "Autenticación no configurada"
        ) from None
    if (
        issuer_parts.scheme != "https"
        or not issuer_parts.hostname
        or issuer_parts.username
        or issuer_parts.password
        or issuer_parts.query
        or issuer_parts.fragment
        or "*" in issuer
        or not audience
        or len(audience) > 128
        or not (jwks_url or public_key_file)
        or (jwks_parts is not None and (jwks_parts.scheme != "https" or not jwks_parts.hostname))
    ):
        raise DomainError(503, "authentication-unavailable", "Autenticación no configurada")
    if public_key_file and (not expected_key_id or not _KEY_ID.fullmatch(expected_key_id)):
        raise DomainError(503, "authentication-unavailable", "Autenticación no configurada")
    try:
        pinned = Path(public_key_file).read_text(encoding="ascii") if public_key_file else None
    except (OSError, UnicodeError):
        raise DomainError(
            503, "authentication-unavailable", "Autenticación no configurada"
        ) from None
    client = PyJWKClient(jwks_url, cache_keys=True) if jwks_url else None
    return issuer, audience, pinned, jwks_url, client, expected_key_id


def authenticate(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        issuer, audience, pinned, _jwks_url, client, expected_key_id = _verifier()
    except DomainError:
        raise
    except OSError:
        raise DomainError(
            503, "authentication-unavailable", "Autenticación no configurada"
        ) from None
    try:
        header = jwt.get_unverified_header(credentials.credentials)
        key_id = header.get("kid")
        if (
            header.get("alg") != "RS256"
            or not isinstance(key_id, str)
            or not key_id
            or len(key_id) > 80
            or (expected_key_id is not None and key_id != expected_key_id)
        ):
            raise _unauthorized()
        key = (
            pinned
            if pinned is not None
            else client.get_signing_key_from_jwt(credentials.credentials).key
        )
        claims = jwt.decode(
            credentials.credentials,
            key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={
                "require": [
                    "exp",
                    "iat",
                    "nbf",
                    "iss",
                    "sub",
                    "aud",
                    "actor_iss",
                    "token_use",
                    "permissions",
                    "sources",
                    "jti",
                ]
            },
        )
    except (InvalidTokenError, PyJWKClientError, OSError, ValueError):
        raise _unauthorized() from None

    permissions_claim, sources_claim = claims["permissions"], claims["sources"]
    if (
        claims.get("token_use") != "persons_delegation_v1"
        or not isinstance(claims.get("actor_iss"), str)
        or not claims["actor_iss"].startswith("https://")
        or not isinstance(claims.get("jti"), str)
        or not claims["jti"]
        or not isinstance(permissions_claim, list)
        or not permissions_claim
        or any(
            not isinstance(item, str) or item not in _ALLOWED_PERMISSIONS
            for item in permissions_claim
        )
        or not isinstance(sources_claim, list)
        or len(sources_claim) > 64
        or any(
            not isinstance(item, str) or len(item) > 64 or not _SOURCE_ALIAS.fullmatch(item)
            for item in sources_claim
        )
        or len(set(permissions_claim)) != len(permissions_claim)
        or len(set(sources_claim)) != len(sources_claim)
        or int(claims["exp"]) - int(claims["iat"]) > 120
    ):
        raise _unauthorized()
    subject, token_issuer = str(claims["sub"]), str(claims["actor_iss"])
    if not subject or len(subject) > 255:
        raise _unauthorized()
    actor_id = uuid5(NAMESPACE_URL, f"{token_issuer}\x00{subject}")
    return Principal(
        actor_id=actor_id,
        permissions=frozenset(permissions_claim),
        source_systems=frozenset(sources_claim),
        issuer=token_issuer,
        subject=subject,
    )


def ownership_verifier():
    # Account linking remains denied; PER-002 does not verify ownership.
    return DenyOwnership()


def require(principal: Principal, permission: str) -> None:
    from .errors import forbidden

    if permission not in principal.permissions:
        _security_logger.warning(
            "authorization_denied actor_id=%s permission=%s",
            principal.actor_id,
            permission,
        )
        raise forbidden()


def log_resource_denial(principal: Principal, action: str) -> None:
    """Log a denied attempt with the same pseudonymous audit actor, never request data."""
    _security_logger.warning(
        "resource_access_denied actor_id=%s action=%s", principal.actor_id, action
    )
