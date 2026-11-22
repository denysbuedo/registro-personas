import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Alias = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$",
        max_length=64,
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PersonStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNDER_REVIEW = "UNDER_REVIEW"


class Provenance(StrictModel):
    system: Alias
    evidence_ref: UUID
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value


class SourceInput(StrictModel):
    system: Alias
    entity: Alias
    external_id: str = Field(min_length=1, max_length=200)

    @field_validator("external_id")
    @classmethod
    def opaque_reference(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid reference")
        return value


class PersonCreate(StrictModel):
    display_name: str = Field(min_length=1, max_length=200)
    source: SourceInput
    provenance: Provenance

    @field_validator("display_name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid name")
        return value.strip()


class PersonView(StrictModel):
    person_id: UUID
    status: PersonStatus
    display_name: str
    created_at: datetime
    updated_at: datetime


class PersonPage(StrictModel):
    items: list[PersonView]
    limit: int
    offset: int
    total: int


class SourceReferenceView(StrictModel):
    system: Alias
    entity: Alias
    external_id: str


class SourceReferenceLink(StrictModel):
    source: SourceInput
    provenance: Provenance

    @model_validator(mode="after")
    def provenance_matches_source(self):
        if self.source.system != self.provenance.system:
            raise ValueError("provenance system must match source system")
        return self


class ReviewCaseView(StrictModel):
    case_id: UUID
    kind: str
    requested_person_id: UUID | None
    created_at: datetime


class ReviewCasePage(StrictModel):
    items: list[ReviewCaseView]
    limit: int
    offset: int
    total: int


class AccountLink(StrictModel):
    issuer: str = Field(min_length=1, max_length=500)
    subject: str = Field(min_length=1, max_length=255)
    verification_ref: UUID
    provenance: Provenance

    @field_validator("issuer")
    @classmethod
    def exact_https_issuer(cls, value: str) -> str:
        # Preserve issuer byte-for-byte; never canonicalize OIDC identifiers.
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or re.search(r"[\s\\\x00-\x1f\x7f]", value)
        ):
            raise ValueError("invalid issuer")
        try:
            _ = parts.port
        except ValueError:
            raise ValueError("invalid issuer") from None
        return value

    @field_validator("subject")
    @classmethod
    def exact_subject(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid subject")
        return value


class AccountView(StrictModel):
    account_id: UUID
    person_id: UUID
    issuer: str
    subject: str
    state: str
    linked_at: datetime


class Problem(StrictModel):
    type: str
    title: str
    status: int
    correlation_id: UUID
