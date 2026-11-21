from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import DomainError, conflict, forbidden, not_found
from .models import (
    AuditRecord,
    DigitalAccount,
    IdempotencyRecord,
    Person,
    ReviewCase,
    SourceReference,
)
from .schemas import (
    AccountLink,
    AccountView,
    PersonCreate,
    PersonPage,
    PersonView,
    ReviewCasePage,
    ReviewCaseView,
    SourceInput,
    SourceReferenceLink,
    SourceReferenceView,
)
from .security import OwnershipVerifier, Principal, log_resource_denial, require


class Registry:
    """Unit of work owns transactions; callers provide a fresh request-scoped Session."""

    def __init__(self, session: Session, principal: Principal, correlation_id: UUID):
        self.session = session
        self.principal = principal
        self.correlation_id = correlation_id

    @staticmethod
    def _source_lock_key(source: SourceInput) -> int:
        return int.from_bytes(
            hashlib.sha256(
                f"source:{source.system}:{source.entity}:{source.external_id}".encode()
            ).digest()[:8],
            "big",
            signed=True,
        )

    def _audit(
        self, action: str, resource_id: UUID | None = None, verification_ref: UUID | None = None
    ) -> None:
        self.session.add(
            AuditRecord(
                actor_id=self.principal.actor_id,
                action=action,
                resource_id=resource_id,
                correlation_id=self.correlation_id,
                verification_ref=verification_ref,
                actor_issuer=self.principal.issuer,
                actor_subject=self.principal.subject,
            )
        )

    def _review(
        self,
        kind: str,
        *,
        source: SourceInput | None = None,
        link: AccountLink | None = None,
        person_id: UUID | None = None,
    ) -> None:
        # Only opaque case metadata; never leak the existing identity or store raw payload in logs.
        with self.session.begin():
            resource_id = None
            if source is not None:
                resource_id = self.session.scalar(
                    select(SourceReference.reference_id).where(
                        SourceReference.system == source.system,
                        SourceReference.entity == source.entity,
                        SourceReference.external_id == source.external_id,
                    )
                )
            if link is not None:
                resource_id = self.session.scalar(
                    select(DigitalAccount.account_id).where(
                        DigitalAccount.issuer == link.issuer,
                        DigitalAccount.subject == link.subject,
                    )
                )
            self.session.add(
                ReviewCase(
                    kind=kind,
                    actor_id=self.principal.actor_id,
                    correlation_id=self.correlation_id,
                    resource_id=resource_id,
                    requested_person_id=person_id,
                )
            )
            self._audit("conflict_detected", resource_id)

    def create(self, data: PersonCreate, key: UUID) -> PersonView:
        require(self.principal, "persons.create")
        if (
            data.source.system not in self.principal.source_systems
            or data.provenance.system != data.source.system
        ):
            log_resource_denial(self.principal, "person_create_source")
            raise forbidden()
        payload = data.model_dump(mode="json")
        # Serialize same principal/key before lookup. DB uniqueness handles distinct keys/sources.
        lock = int.from_bytes(
            hashlib.sha256(f"create:{self.principal.actor_id}:{key}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        try:
            with self.session.begin():
                self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
                previous = self.session.get(IdempotencyRecord, (self.principal.actor_id, key))
                if previous:
                    if previous.request_payload != payload:
                        raise DomainError(
                            409, "idempotency-conflict", "Clave de idempotencia en conflicto"
                        )
                    person = self.session.get(Person, previous.person_id)
                    self._audit("person_create_replayed", previous.person_id)
                    return PersonView.model_validate(person)
                source_lock = self._source_lock_key(data.source)
                self.session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": source_lock}
                )
                person = Person(
                    display_name=data.display_name,
                    name_source_system=data.provenance.system,
                    name_evidence_ref=data.provenance.evidence_ref,
                    name_observed_at=data.provenance.observed_at,
                )
                self.session.add(person)
                self.session.flush()
                self.session.add(
                    SourceReference(
                        person_id=person.person_id,
                        **data.source.model_dump(),
                        evidence_ref=data.provenance.evidence_ref,
                        observed_at=data.provenance.observed_at,
                    )
                )
                self.session.flush()
                self.session.add(
                    IdempotencyRecord(
                        actor_id=self.principal.actor_id,
                        key=key,
                        request_payload=payload,
                        person_id=person.person_id,
                    )
                )
                self._audit("person_created", person.person_id)
                return PersonView.model_validate(person)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise
            self._review("source_reference_collision", source=data.source)
            raise conflict() from None

    def get(self, person_id: UUID) -> PersonView:
        require(self.principal, "persons.read")
        if not self._can_read(person_id):
            log_resource_denial(self.principal, "person_read")
            raise not_found()
        person = self.session.get(Person, person_id)
        if person is None:
            raise not_found()
        self._audit("person_read", person_id)
        self.session.commit()
        return PersonView.model_validate(person)

    def _can_read(self, person_id: UUID) -> bool:
        if person_id in self.principal.readable_person_ids:
            return True
        return (
            bool(self.principal.source_systems)
            and self.session.scalar(
                select(Person.person_id)
                .join(SourceReference, SourceReference.person_id == Person.person_id)
                .where(
                    Person.person_id == person_id,
                    Person.name_source_system.in_(self.principal.source_systems),
                    SourceReference.system.in_(self.principal.source_systems),
                )
                .limit(1)
            )
            is not None
        )

    def resolve(self, source: SourceInput) -> PersonView:
        require(self.principal, "persons.read")
        if source.system not in self.principal.source_systems:
            log_resource_denial(self.principal, "person_resolve_source")
            raise not_found()
        person_id = self.session.scalar(
            select(SourceReference.person_id).where(
                SourceReference.system == source.system,
                SourceReference.entity == source.entity,
                SourceReference.external_id == source.external_id,
            )
        )
        if person_id is None:
            log_resource_denial(self.principal, "person_resolve_not_found")
            raise not_found()
        result = self.get(person_id)
        self._audit("person_resolved_by_source", person_id)
        self.session.commit()
        return result

    def list(self, limit: int, offset: int) -> PersonPage:
        require(self.principal, "persons.list")
        require(self.principal, "persons.read")
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise DomainError(422, "invalid-pagination", "Paginación inválida")
        permitted = Person.person_id.in_(self.principal.readable_person_ids)
        if self.principal.source_systems:
            permitted = permitted | (
                Person.name_source_system.in_(self.principal.source_systems)
                & Person.person_id.in_(
                    select(SourceReference.person_id).where(
                        SourceReference.system.in_(self.principal.source_systems)
                    )
                )
            )
        total = self.session.scalar(select(func.count()).select_from(Person).where(permitted))
        rows = self.session.scalars(
            select(Person).where(permitted).order_by(Person.person_id).limit(limit).offset(offset)
        ).all()
        self._audit("person_listed")
        self.session.commit()
        return PersonPage(
            items=[PersonView.model_validate(p) for p in rows],
            total=total or 0,
            limit=limit,
            offset=offset,
        )

    def references(self, person_id: UUID) -> list[SourceReferenceView]:
        require(self.principal, "persons.read")
        if not self._can_read(person_id):
            log_resource_denial(self.principal, "person_references_read")
            raise not_found()
        rows = self.session.scalars(
            select(SourceReference)
            .where(
                SourceReference.person_id == person_id,
                SourceReference.system.in_(self.principal.source_systems),
            )
            .order_by(SourceReference.system, SourceReference.entity, SourceReference.reference_id)
        ).all()
        self._audit("person_references_read", person_id)
        self.session.commit()
        return [SourceReferenceView.model_validate(row) for row in rows]

    def add_reference(
        self, person_id: UUID, data: SourceReferenceLink
    ) -> tuple[SourceReferenceView, bool]:
        require(self.principal, "persons.review")
        require(self.principal, "persons.read")
        require(self.principal, "persons.references.link")
        if (
            data.source.system not in self.principal.source_systems
            or data.provenance.system != data.source.system
        ):
            log_resource_denial(self.principal, "person_reference_link_source")
            raise forbidden()

        conflict_detected = False
        linked: SourceReference | None = None
        created = False
        try:
            with self.session.begin():
                source_lock = self._source_lock_key(data.source)
                self.session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": source_lock}
                )
                if not self._can_read(person_id) or self.session.get(Person, person_id) is None:
                    log_resource_denial(self.principal, "person_reference_link_target")
                    raise not_found()

                existing = self.session.scalar(
                    select(SourceReference).where(
                        SourceReference.system == data.source.system,
                        SourceReference.entity == data.source.entity,
                        SourceReference.external_id == data.source.external_id,
                    )
                )
                if existing is not None and existing.person_id != person_id:
                    conflict_detected = True
                elif existing is not None:
                    linked = existing
                    self._audit(
                        "person_reference_link_replayed",
                        existing.reference_id,
                        data.provenance.evidence_ref,
                    )
                else:
                    linked = SourceReference(
                        person_id=person_id,
                        **data.source.model_dump(),
                        evidence_ref=data.provenance.evidence_ref,
                        observed_at=data.provenance.observed_at,
                    )
                    self.session.add(linked)
                    self.session.flush()
                    self._audit(
                        "person_reference_linked",
                        linked.reference_id,
                        data.provenance.evidence_ref,
                    )
                    created = True
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise
            self._review("source_reference_collision", source=data.source)
            raise conflict() from None

        if conflict_detected:
            self._review("source_reference_collision", source=data.source)
            raise conflict()
        return SourceReferenceView.model_validate(linked), created

    def reviews(self, limit: int, offset: int) -> ReviewCasePage:
        require(self.principal, "persons.review")
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise DomainError(422, "invalid-pagination", "Paginación inválida")
        source_cases = (
            ReviewCase.resource_id.in_(
                select(SourceReference.reference_id).where(
                    SourceReference.system.in_(self.principal.source_systems)
                )
            )
            if self.principal.source_systems
            else False
        )
        condition = (
            ReviewCase.state == "PENDING",
            ReviewCase.kind == "source_reference_collision",
            source_cases,
        )
        total = (
            self.session.scalar(select(func.count()).select_from(ReviewCase).where(*condition)) or 0
        )
        rows = self.session.scalars(
            select(ReviewCase)
            .where(*condition)
            .order_by(ReviewCase.created_at, ReviewCase.case_id)
            .limit(limit)
            .offset(offset)
        ).all()
        self._audit("review_cases_listed")
        self.session.commit()
        return ReviewCasePage(
            items=[ReviewCaseView.model_validate(row) for row in rows],
            limit=limit,
            offset=offset,
            total=total,
        )

    def link(
        self, person_id: UUID, data: AccountLink, verifier: OwnershipVerifier
    ) -> tuple[AccountView, bool]:
        require(self.principal, "accounts.link")
        if person_id not in self.principal.linkable_person_ids:
            raise not_found()
        if data.provenance.system not in self.principal.source_systems:
            raise forbidden()
        try:
            with self.session.begin():
                if self.session.get(Person, person_id) is None:
                    raise not_found()
                # Verifier is supplied by trusted integration, not an HTTP flag or possession of UUID.
                if not verifier.verify(self.principal, person_id, data):
                    raise forbidden()
                lock = int.from_bytes(
                    hashlib.sha256((data.issuer + "\x00" + data.subject).encode()).digest()[:8],
                    "big",
                    signed=True,
                )
                self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
                existing = self.session.scalar(
                    select(DigitalAccount).where(
                        DigitalAccount.issuer == data.issuer,
                        DigitalAccount.subject == data.subject,
                    )
                )
                if existing:
                    if existing.person_id != person_id or existing.state != "ACTIVE":
                        # Roll back this transaction, then persist a restricted review marker.
                        raise conflict()
                    self._audit(
                        "account_link_reverified", existing.account_id, data.verification_ref
                    )
                    return AccountView.model_validate(existing), False
                account = DigitalAccount(
                    person_id=person_id,
                    issuer=data.issuer,
                    subject=data.subject,
                    source_system=data.provenance.system,
                    evidence_ref=data.provenance.evidence_ref,
                    verification_ref=data.verification_ref,
                    observed_at=data.provenance.observed_at,
                )
                self.session.add(account)
                self.session.flush()
                self._audit("account_linked", account.account_id, data.verification_ref)
                return AccountView.model_validate(account), True
        except DomainError as exc:
            if exc.code == "identity-conflict":
                self._review("digital_account_collision", link=data, person_id=person_id)
            raise
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise
            self._review("digital_account_collision", link=data, person_id=person_id)
            raise conflict() from None
