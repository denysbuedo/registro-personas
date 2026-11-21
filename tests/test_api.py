import logging
from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from registro_personas.api import create_app
from registro_personas.db import get_session
from registro_personas.schemas import (
    AccountView,
    PersonCreate,
    PersonPage,
    PersonView,
    Problem,
    ReviewCasePage,
    SourceReferenceLink,
    SourceReferenceView,
)
from registro_personas.security import authenticate, ownership_verifier

from .conftest import ROOT
from .fakes import EvidenceVerifier
from .test_registry import link_input


@pytest.fixture
def api(db_engine, principal):
    app, state = create_app(), {"principal": principal}

    def session():
        with Session(db_engine, expire_on_commit=False) as value:
            yield value

    app.dependency_overrides[authenticate] = lambda: state["principal"]
    app.dependency_overrides[get_session] = session
    verifier = EvidenceVerifier()
    app.dependency_overrides[ownership_verifier] = lambda: verifier
    with TestClient(app) as client:
        yield client, state, verifier


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/v1/personas", None),
        ("get", "/v1/personas/00000000-0000-4000-8000-000000000001", None),
        ("get", "/v1/personas/00000000-0000-4000-8000-000000000001/referencias", None),
        ("get", "/v1/revisiones", None),
        (
            "get",
            "/v1/referencias/resolver?system=demo&entity=fixture&external_id=PRIVATE-SYNTHETIC",
            None,
        ),
        ("post", "/v1/personas", {}),
        ("post", "/v1/personas/00000000-0000-4000-8000-000000000001/cuentas", {}),
    ],
)
def test_default_denial_before_database(method, path, body):
    app = create_app()
    database = Mock(side_effect=AssertionError("Denied requests must not connect to DB"))
    app.dependency_overrides[get_session] = database
    with TestClient(app) as client:
        response = client.request(
            method,
            path,
            json=body,
            headers={
                "Authorization": "Bearer SYNTHETIC-NOT-A-CREDENTIAL",
                "X-Actor-Id": str(uuid4()),
                "X-Permissions": "persons.create,accounts.link",
            },
        )
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE-SYNTHETIC" not in response.text
    assert response.json()["status"] == 503
    database.assert_not_called()


def test_api_create_query_link_and_generic_conflict(api, person_data):
    client, state, verifier = api
    key = str(uuid4())
    correlation_id = str(uuid4())
    headers = {"Idempotency-Key": key, "X-Correlation-ID": correlation_id}
    body = person_data.model_dump(mode="json")
    created = client.post("/v1/personas", json=body, headers=headers)
    assert created.status_code == 201
    assert created.headers["X-Correlation-ID"] == correlation_id
    person = PersonView.model_validate(created.json())
    assert client.post("/v1/personas", json=body, headers=headers).json() == created.json()
    assert client.get(f"/v1/personas/{person.person_id}").json() == created.json()
    state["principal"] = replace(
        state["principal"],
        readable_person_ids=frozenset({person.person_id}),
        linkable_person_ids=frozenset({person.person_id}),
    )
    assert client.get(f"/v1/personas/{person.person_id}").json() == created.json()
    refs = client.get(f"/v1/personas/{person.person_id}/referencias")
    assert refs.status_code == 200
    assert [SourceReferenceView.model_validate(row).system for row in refs.json()] == ["demo"]
    page = client.get("/v1/personas", params={"limit": 1}).json()
    assert page["total"] == 1 and page["items"] == [created.json()]
    assert client.get("/v1/referencias/resolver", params=body["source"]).json() == created.json()
    conflict = client.post("/v1/personas", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert conflict.status_code == 409
    assert str(person.person_id) not in conflict.text and body["display_name"] not in conflict.text
    state["principal"] = replace(
        state["principal"], permissions=state["principal"].permissions | {"persons.review"}
    )
    reviews = client.get("/v1/revisiones")
    assert reviews.status_code == 200
    review_page = ReviewCasePage.model_validate(reviews.json())
    assert review_page.total == 1 and review_page.items[0].kind == "source_reference_collision"
    link = link_input()
    route = f"/v1/personas/{person.person_id}/cuentas"
    assert client.post(route, json=link.model_dump(mode="json")).status_code == 403
    verifier.approve(state["principal"], person.person_id, link)
    account = client.post(route, json=link.model_dump(mode="json"))
    assert account.status_code == 201
    assert account.json()["person_id"] == str(person.person_id)
    assert "verification_ref" not in account.json()
    assert client.post(route, json=link.model_dump(mode="json")).status_code == 200


def test_denied_permissions_and_validation_do_not_leak(api, person_data, caplog):
    client, state, _ = api
    state["principal"] = replace(state["principal"], permissions=frozenset())
    caplog.set_level(logging.INFO, logger="registro_personas")
    response = client.post(
        "/v1/personas",
        json=person_data.model_dump(mode="json"),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 403
    assert client.get("/v1/personas").status_code == 403
    body = {"display_name": "SYNTHETIC-PRIVATE-NAME", "secret": "SYNTHETIC-PRIVATE-SECRET"}
    invalid = client.post("/v1/personas", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert invalid.status_code == 422
    assert "SYNTHETIC-PRIVATE" not in invalid.text
    application_logs = " ".join(
        record.message for record in caplog.records if record.name == "registro_personas"
    )
    assert "SYNTHETIC-PRIVATE" not in application_logs
    assert person_data.display_name not in application_logs
    assert person_data.source.external_id not in application_logs
    assert client.get("/v1/personas?limit=101").status_code == 422
    assert client.get("/v1/personas?offset=10001").status_code == 422


def test_error_sanitization_and_correlation():
    app = create_app()

    @app.get("/synthetic-failure")
    def failure():
        raise SQLAlchemyError("SYNTHETIC-PRIVATE-SQL-PAYLOAD")

    @app.get("/synthetic-unexpected")
    def unexpected():
        raise RuntimeError("SYNTHETIC-PRIVATE-EXCEPTION")

    with TestClient(app) as client:
        for path, status in [("/synthetic-failure", 503), ("/synthetic-unexpected", 500)]:
            response = client.get(path, headers={"X-Correlation-ID": "SYNTHETIC-PRIVATE-INVALID"})
            assert response.status_code == status
            problem = Problem.model_validate(response.json())
            assert str(problem.correlation_id) == response.headers["X-Correlation-ID"]
            assert "SYNTHETIC-PRIVATE" not in response.text


def test_local_contract_matches_executable_schema():
    with (ROOT / "docs/openapi.yaml").open(encoding="utf-8") as file:
        contract = yaml.safe_load(file)
    with TestClient(create_app()) as client:
        executable = client.get("/openapi.json").json()
    for path, operations in contract["paths"].items():
        assert path in executable["paths"]
        for method, operation in operations.items():
            if method not in {"get", "post"}:
                continue
            actual = executable["paths"][path][method]
            assert "x-access-control" in actual
            for code in operation["responses"]:
                if str(code) in actual["responses"] and str(code).isdigit() and int(code) >= 400:
                    assert "application/problem+json" in actual["responses"][str(code)]["content"]
    for model in [
        PersonCreate,
        PersonView,
        PersonPage,
        AccountView,
        Problem,
        SourceReferenceView,
        SourceReferenceLink,
        ReviewCasePage,
    ]:
        name = model.__name__
        documented = contract["components"]["schemas"][name]
        actual = executable["components"]["schemas"][name]
        assert set(documented["required"]) == set(actual["required"])
        assert set(documented["properties"]) == set(actual["properties"])
    # Backend requires the standard bearer token and does not accept identity/permission headers.
    assert "HTTPBearer" in executable.get("components", {}).get("securitySchemes", {})
    assert not any("contribuciones" in path for path in executable["paths"])


def test_safe_entrypoint_disables_access_logging(monkeypatch):
    from registro_personas import __main__

    run = Mock()
    monkeypatch.setattr(__main__.uvicorn, "run", run)
    __main__.main([])
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["access_log"] is False


def test_platform_port_and_readiness_stay_closed(monkeypatch):
    from registro_personas import __main__

    run = Mock()
    monkeypatch.setattr(__main__.uvicorn, "run", run)
    __main__.main(["--port", "8003"])
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8003, "access_log": False}
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "authentication_not_configured"


@pytest.mark.parametrize("database_ready", [True, False])
def test_readiness_checks_database_connection(monkeypatch, database_ready):
    import registro_personas.api as api_module

    monkeypatch.setenv("REGISTRY_DATABASE_URL", "postgresql+psycopg://local@127.0.0.1/test")
    monkeypatch.setattr(
        api_module, "_verifier", lambda: ("issuer", "audience", None, None, None, None)
    )
    monkeypatch.setattr(api_module, "database_is_ready", lambda: database_ready)
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")
    if database_ready:
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
    else:
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "reason": "database_unavailable",
        }


@pytest.mark.parametrize("port", ["0", "65536", "https://invalid.example.invalid"])
def test_invalid_launch_ports_fail_before_start(monkeypatch, port):
    from registro_personas import __main__

    run = Mock()
    monkeypatch.setattr(__main__.uvicorn, "run", run)
    with pytest.raises(SystemExit):
        __main__.main(["--port", port])
    run.assert_not_called()
