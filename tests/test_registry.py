from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from registro_personas.errors import DomainError
from registro_personas.models import (
    AuditRecord,
    DigitalAccount,
    InstitutionalAttribute,
    Person,
    ReviewCase,
    SourceReference,
)
from registro_personas.schemas import AccountLink, SourceReferenceLink
from registro_personas.security import DenyOwnership

from .conftest import invoke
from .fakes import EvidenceVerifier


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def changed(data, external_id="SYNTHETIC-002"):
    return data.model_copy(
        update={"source": data.source.model_copy(update={"external_id": external_id})}
    )


def link_input():
    return AccountLink.model_validate(
        {
            "issuer": "https://issuer.example.invalid/realms/demo",
            "subject": "SUBJECT-SYNTHETIC",
            "verification_ref": str(uuid4()),
            "provenance": {
                "system": "demo",
                "evidence_ref": str(uuid4()),
                "observed_at": "2026-11-19T12:00:00Z",
            },
        }
    )


def test_create_idempotency_and_source_conflict(db_engine, principal, person_data):
    key = uuid4()
    first = invoke(db_engine, principal, "create", person_data, key)
    assert first.person_id.version == 4
    assert first.status == "ACTIVE"
    assert first.created_at.tzinfo is not None
    assert invoke(db_engine, principal, "create", person_data, key) == first
    assert count(db_engine, Person) == count(db_engine, SourceReference) == 1
    assert count(db_engine, AuditRecord) == 2
    with pytest.raises(DomainError) as error:
        invoke(db_engine, principal, "create", changed(person_data), key)
    assert error.value.code == "idempotency-conflict"
    with pytest.raises(DomainError) as error:
        invoke(db_engine, principal, "create", person_data, uuid4())
    assert error.value.status == 409
    assert str(first.person_id) not in str(error.value)
    assert count(db_engine, Person) == 1  # Failed transaction leaves no orphan person.
    with Session(db_engine) as session:
        case = session.scalar(select(ReviewCase))
        assert case.state == "PENDING" and case.resource_id is not None


def test_same_name_does_not_merge_and_idempotency_is_actor_scoped(
    db_engine, principal, person_data
):
    key = uuid4()
    first = invoke(db_engine, principal, "create", person_data, key)
    second = invoke(
        db_engine, replace(principal, actor_id=uuid4()), "create", changed(person_data), key
    )
    assert first.person_id != second.person_id
    assert first.display_name == second.display_name


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_creates(db_engine, principal, person_data, same_key):
    barrier, shared_key = Barrier(2), uuid4()

    def create():
        barrier.wait(timeout=10)
        try:
            return invoke(
                db_engine, principal, "create", person_data, shared_key if same_key else uuid4()
            ).person_id
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert count(db_engine, Person) == count(db_engine, SourceReference) == 1
    if same_key:
        assert results[0] == results[1]
    else:
        assert results.count(409) == 1
        assert count(db_engine, ReviewCase) == 1


def test_read_resolve_and_filtered_pagination(db_engine, principal, person_data):
    one = invoke(db_engine, principal, "create", person_data, uuid4())
    two = invoke(db_engine, principal, "create", changed(person_data), uuid4())
    resource_only = replace(
        principal,
        source_systems=frozenset(),
        readable_person_ids=frozenset({one.person_id}),
    )
    assert invoke(db_engine, resource_only, "get", one.person_id) == one
    for target in [two.person_id, uuid4()]:
        with pytest.raises(DomainError) as error:
            invoke(db_engine, resource_only, "get", target)
        assert error.value.status == 404
    with pytest.raises(DomainError) as error:
        invoke(db_engine, resource_only, "resolve", person_data.source)
    assert error.value.status == 404
    page = invoke(db_engine, resource_only, "list", 1, 0)
    assert page.items == [one] and page.total == 1
    assert invoke(db_engine, resource_only, "list", 1, 1).items == []
    source_principal = replace(principal, readable_person_ids=frozenset({one.person_id}))
    assert invoke(db_engine, source_principal, "resolve", person_data.source) == one
    assert invoke(db_engine, source_principal, "resolve", changed(person_data).source) == two
    assert invoke(db_engine, source_principal, "list", 20, 0).total == 2
    assert invoke(db_engine, principal, "list", 20, 0).total == 2


def test_add_source_reference_authorization_replay_collision_and_audit(
    db_engine, principal, person_data
):
    person = invoke(db_engine, principal, "create", person_data, uuid4())
    reviewer = replace(
        principal,
        permissions=principal.permissions | {"persons.review", "persons.references.link"},
    )
    link_data = SourceReferenceLink.model_validate(
        {
            "source": {"system": "demo", "entity": "fixture", "external_id": "SYNTHETIC-NEW"},
            "provenance": {
                "system": "demo",
                "evidence_ref": str(uuid4()),
                "observed_at": "2026-11-20T12:00:00Z",
            },
        }
    )

    linked, created = invoke(db_engine, reviewer, "add_reference", person.person_id, link_data)
    assert created and linked.external_id == "SYNTHETIC-NEW"
    assert len(invoke(db_engine, reviewer, "references", person.person_id)) == 2
    replayed, created = invoke(db_engine, reviewer, "add_reference", person.person_id, link_data)
    assert not created and replayed == linked

    second_person = invoke(db_engine, principal, "create", changed(person_data), uuid4())
    collision_data = link_data.model_copy(update={"source": person_data.source})
    with pytest.raises(DomainError) as error:
        invoke(db_engine, reviewer, "add_reference", second_person.person_id, collision_data)
    assert error.value.status == 409
    assert str(person.person_id) not in str(error.value)
    assert count(db_engine, Person) == 2
    assert count(db_engine, SourceReference) == 3
    with Session(db_engine) as session:
        cases = list(session.scalars(select(ReviewCase)))
        source_ref = session.scalar(
            select(SourceReference).where(SourceReference.external_id == "SYNTHETIC-NEW")
        )
        create_audit = session.scalar(
            select(AuditRecord).where(AuditRecord.action == "person_reference_linked")
        )
        replay_audit = session.scalar(
            select(AuditRecord).where(AuditRecord.action == "person_reference_link_replayed")
        )
    assert len(cases) == 1 and cases[0].requested_person_id is None
    assert create_audit.resource_id == replay_audit.resource_id == source_ref.reference_id
    assert (
        create_audit.verification_ref
        == replay_audit.verification_ref
        == link_data.provenance.evidence_ref
    )


def test_add_source_reference_requires_review_and_target_read_access(
    db_engine, principal, person_data
):
    person = invoke(db_engine, principal, "create", person_data, uuid4())
    link_data = SourceReferenceLink.model_validate(
        {
            "source": {"system": "demo", "entity": "fixture", "external_id": "SYNTHETIC-NEW"},
            "provenance": {
                "system": "demo",
                "evidence_ref": str(uuid4()),
                "observed_at": "2026-11-20T12:00:00Z",
            },
        }
    )
    source_b_actor = replace(
        principal,
        permissions=principal.permissions | {"persons.review", "persons.references.link"},
        source_systems=frozenset({"other"}),
    )
    other_source_link = link_data.model_copy(
        update={
            "source": link_data.source.model_copy(update={"system": "other"}),
            "provenance": link_data.provenance.model_copy(update={"system": "other"}),
        }
    )
    with pytest.raises(DomainError) as error:
        invoke(db_engine, source_b_actor, "add_reference", person.person_id, other_source_link)
    assert error.value.status == 404  # Source B authority cannot read this Source A person.

    with pytest.raises(DomainError) as error:
        invoke(db_engine, principal, "add_reference", person.person_id, link_data)
    assert error.value.status == 403
    assert count(db_engine, SourceReference) == 1


def test_create_and_add_reference_race_returns_reviewable_conflict(
    db_engine, principal, person_data
):
    target = invoke(db_engine, principal, "create", person_data, uuid4())
    reviewer = replace(
        principal,
        permissions=principal.permissions | {"persons.review", "persons.references.link"},
    )
    raced_data = changed(person_data, external_id="SYNTHETIC-CROSS-OPERATION-RACE")
    link_data = SourceReferenceLink.model_validate(
        {
            "source": raced_data.source.model_dump(mode="json"),
            "provenance": {
                "system": "demo",
                "evidence_ref": str(uuid4()),
                "observed_at": "2026-11-20T12:00:00Z",
            },
        }
    )
    barrier = Barrier(2)

    def create():
        barrier.wait(timeout=10)
        try:
            invoke(db_engine, principal, "create", raced_data, uuid4())
            return "created"
        except DomainError as error:
            return error.status

    def add_reference():
        barrier.wait(timeout=10)
        try:
            invoke(db_engine, reviewer, "add_reference", target.person_id, link_data)
            return "linked"
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda operation: operation(), [create, add_reference]))

    assert sorted(str(result) for result in results) == ["409", "created"] or sorted(
        str(result) for result in results
    ) == ["409", "linked"]
    assert count(db_engine, SourceReference) == 2
    assert count(db_engine, ReviewCase) == 1


def test_create_source_authorization(db_engine, principal, person_data):
    for actor, payload in [
        (replace(principal, source_systems=frozenset()), person_data),
        (
            principal,
            person_data.model_copy(
                update={"provenance": person_data.provenance.model_copy(update={"system": "other"})}
            ),
        ),
        (replace(principal, permissions=frozenset()), person_data),
    ]:
        with pytest.raises(DomainError) as error:
            invoke(db_engine, actor, "create", payload, uuid4())
        assert error.value.status == 403
    assert count(db_engine, Person) == 0


def test_link_verified_replay_conflict_and_audit(db_engine, principal, person_data):
    one = invoke(db_engine, principal, "create", person_data, uuid4())
    two = invoke(db_engine, principal, "create", changed(person_data), uuid4())
    actor = replace(principal, linkable_person_ids=frozenset({one.person_id, two.person_id}))
    data, verifier = link_input(), EvidenceVerifier()
    with pytest.raises(DomainError) as error:
        invoke(db_engine, actor, "link", one.person_id, data, DenyOwnership())
    assert error.value.status == 403
    verifier.approve(actor, one.person_id, data)
    first, created = invoke(db_engine, actor, "link", one.person_id, data, verifier)
    assert created and first.person_id == one.person_id
    replay, created = invoke(db_engine, actor, "link", one.person_id, data, verifier)
    assert not created and replay.account_id == first.account_id
    verifier.approve(actor, two.person_id, data)
    with pytest.raises(DomainError) as error:
        invoke(db_engine, actor, "link", two.person_id, data, verifier)
    assert error.value.status == 409
    assert count(db_engine, DigitalAccount) == 1
    with Session(db_engine) as session:
        account = session.scalar(select(DigitalAccount))
        assert account.person_id == one.person_id
        case = session.scalar(select(ReviewCase))
        assert case.resource_id == first.account_id and case.requested_person_id == two.person_id
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRecord)
                .where(AuditRecord.action == "account_linked")
            )
            == 1
        )
        assert (
            session.scalar(
                select(AuditRecord.verification_ref).where(
                    AuditRecord.action == "account_link_reverified"
                )
            )
            == data.verification_ref
        )


@pytest.mark.parametrize(
    "mutation", ["actor", "person", "issuer", "subject", "verification", "evidence"]
)
def test_ownership_double_binds_all_identity_dimensions(
    db_engine, principal, person_data, mutation
):
    one = invoke(db_engine, principal, "create", person_data, uuid4())
    two = invoke(db_engine, principal, "create", changed(person_data), uuid4())
    actor = replace(principal, linkable_person_ids=frozenset({one.person_id, two.person_id}))
    data, verifier = link_input(), EvidenceVerifier()
    verifier.approve(actor, one.person_id, data)
    target = one.person_id
    if mutation == "actor":
        actor = replace(actor, actor_id=uuid4())
    elif mutation == "person":
        target = two.person_id
    elif mutation in {"issuer", "subject"}:
        data = data.model_copy(
            update={mutation: "https://other.example.invalid" if mutation == "issuer" else "OTHER"}
        )
    elif mutation == "verification":
        data = data.model_copy(update={"verification_ref": uuid4()})
    else:
        data = data.model_copy(
            update={"provenance": data.provenance.model_copy(update={"evidence_ref": uuid4()})}
        )
    with pytest.raises(DomainError) as error:
        invoke(db_engine, actor, "link", target, data, verifier)
    assert error.value.status == 403
    assert count(db_engine, DigitalAccount) == 0


def test_link_resource_permission_and_revoked_never_reactivated(db_engine, principal, person_data):
    one = invoke(db_engine, principal, "create", person_data, uuid4())
    data, verifier = link_input(), EvidenceVerifier()
    verifier.approve(principal, one.person_id, data)
    with pytest.raises(DomainError) as error:
        invoke(db_engine, principal, "link", one.person_id, data, verifier)
    assert error.value.status == 404
    actor = replace(principal, linkable_person_ids=frozenset({one.person_id}))
    invoke(db_engine, actor, "link", one.person_id, data, verifier)
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE digital_accounts SET state='REVOKED'"))
    with pytest.raises(DomainError) as error:
        invoke(db_engine, actor, "link", one.person_id, data, verifier)
    assert error.value.status == 409


def test_concurrent_account_collision(db_engine, principal, person_data):
    one = invoke(db_engine, principal, "create", person_data, uuid4())
    two = invoke(db_engine, principal, "create", changed(person_data), uuid4())
    actor = replace(principal, linkable_person_ids=frozenset({one.person_id, two.person_id}))
    data, verifier, barrier = link_input(), EvidenceVerifier(), Barrier(2)
    for person_id in [one.person_id, two.person_id]:
        verifier.approve(actor, person_id, data)

    def link(person_id):
        barrier.wait(timeout=10)
        try:
            return invoke(db_engine, actor, "link", person_id, data, verifier)[0].person_id
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(link, [one.person_id, two.person_id]))
    assert results.count(409) == 1
    assert count(db_engine, DigitalAccount) == 1


def test_database_guards_and_attribute_provenance(db_engine, principal, person_data):
    one = invoke(db_engine, principal, "create", person_data, uuid4())
    actor = replace(principal, linkable_person_ids=frozenset({one.person_id}))
    data, verifier = link_input(), EvidenceVerifier()
    verifier.approve(actor, one.person_id, data)
    invoke(db_engine, actor, "link", one.person_id, data, verifier)
    for sql in [
        "UPDATE persons SET person_id = '00000000-0000-4000-8000-000000000001'",
        "UPDATE source_references SET external_id = 'OTHER'",
        "UPDATE digital_accounts SET subject = 'OTHER'",
        "UPDATE digital_accounts SET person_id = '00000000-0000-4000-8000-000000000001'",
        "UPDATE source_references SET person_id = '00000000-0000-4000-8000-000000000001'",
        "UPDATE audit_records SET action='OTHER'",
        "DELETE FROM audit_records",
    ]:
        with pytest.raises(IntegrityError), db_engine.begin() as connection:
            connection.execute(text(sql))
    with Session(db_engine) as session, session.begin():
        session.add(
            InstitutionalAttribute(
                person_id=one.person_id,
                key="demo.fixture",
                value={"text": "SYNTHETIC"},
                source_system="demo",
                evidence_ref=uuid4(),
                observed_at=person_data.provenance.observed_at,
            )
        )
    assert count(db_engine, InstitutionalAttribute) == 1


def test_unreferenced_person_uuid_is_immutable_in_postgresql(db_engine, person_data):
    with Session(db_engine) as session, session.begin():
        session.add(
            Person(
                display_name="Ficticia sin referencias",
                name_source_system="demo",
                name_evidence_ref=uuid4(),
                name_observed_at=person_data.provenance.observed_at,
            )
        )
    with pytest.raises(IntegrityError), db_engine.begin() as connection:
        connection.execute(
            text("UPDATE persons SET person_id='00000000-0000-4000-8000-000000000001'")
        )


def test_no_blank_name_or_naive_provenance(person_data):
    for payload in [
        {**person_data.model_dump(mode="json"), "display_name": "   "},
        {**person_data.model_dump(mode="json"), "person_id": str(uuid4())},
        {
            **person_data.model_dump(mode="json"),
            "provenance": {
                "system": "demo",
                "evidence_ref": str(uuid4()),
                "observed_at": "2026-11-19T12:00:00",
            },
        },
    ]:
        with pytest.raises(ValidationError):
            type(person_data).model_validate(payload)


@pytest.mark.parametrize(
    "issuer",
    [
        "http://issuer.example.invalid",
        "https://user:pass@issuer.example.invalid",
        "https://issuer.example.invalid?x=1",
        "https://issuer.example.invalid/#frag",
        "https://issuer.example.invalid\\evil",
    ],
)
def test_issuer_validation(issuer):
    with pytest.raises(ValidationError):
        AccountLink.model_validate({**link_input().model_dump(mode="json"), "issuer": issuer})
