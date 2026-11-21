from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "persons"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','INACTIVE','UNDER_REVIEW')", name="person_status"),
    )
    person_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    name_source_system: Mapped[str] = mapped_column(String(64))
    name_evidence_ref: Mapped[UUID] = mapped_column(Uuid)
    name_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceReference(Base):
    __tablename__ = "source_references"
    __table_args__ = (
        UniqueConstraint("system", "entity", "external_id", name="uq_source_reference"),
    )
    reference_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), index=True
    )
    system: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(200, collation="C"))
    evidence_ref: Mapped[UUID | None] = mapped_column(Uuid)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DigitalAccount(Base):
    """Association only. Not an account store, no passwords or token material."""

    __tablename__ = "digital_accounts"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_account_identity"),
        CheckConstraint("state IN ('ACTIVE','REVOKED')", name="account_state"),
    )
    account_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), index=True
    )
    issuer: Mapped[str] = mapped_column(String(500, collation="C"))
    subject: Mapped[str] = mapped_column(String(255, collation="C"))
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    source_system: Mapped[str] = mapped_column(String(64))
    evidence_ref: Mapped[UUID] = mapped_column(Uuid)
    verification_ref: Mapped[UUID] = mapped_column(Uuid)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstitutionalAttribute(Base):
    """Initial structure only; no generic contribution endpoint or free-form business profile."""

    __tablename__ = "institutional_attributes"
    __table_args__ = (
        UniqueConstraint("person_id", "key", "source_system", name="uq_attribute_source"),
    )
    attribute_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict] = mapped_column(JSONB)
    source_system: Mapped[str] = mapped_column(String(64))
    evidence_ref: Mapped[UUID] = mapped_column(Uuid)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    actor_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    key: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # Exact validated payload is restricted DB data, never emitted to logs.
    request_payload: Mapped[dict] = mapped_column(JSONB)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.person_id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditRecord(Base):
    __tablename__ = "audit_records"
    audit_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    # Restricted audit identity; never returned by APIs or application logs.
    actor_issuer: Mapped[str | None] = mapped_column(String(500))
    actor_subject: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    verification_ref: Mapped[UUID | None] = mapped_column(Uuid)
    correlation_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewCase(Base):
    __tablename__ = "review_cases"
    __table_args__ = (CheckConstraint("state = 'PENDING'", name="review_state"),)
    case_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    correlation_id: Mapped[UUID] = mapped_column(Uuid)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    requested_person_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_persons_created_id", Person.created_at, Person.person_id)
