import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from .db import database_is_ready, get_session
from .errors import DomainError
from .schemas import (
    AccountLink,
    AccountView,
    PersonCreate,
    PersonPage,
    PersonView,
    Problem,
    ReviewCasePage,
    SourceInput,
    SourceReferenceLink,
    SourceReferenceView,
)
from .security import OwnershipVerifier, Principal, _verifier, authenticate, ownership_verifier
from .service import Registry

logger = logging.getLogger("registro_personas")


def correlation(request: Request) -> UUID:
    return request.state.correlation_id


def registry(
    request: Request,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[Session, Depends(get_session)],
) -> Registry:
    return Registry(session, principal, correlation(request))


RegistryDep = Annotated[Registry, Depends(registry)]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Registro Institucional de Personas",
        version="0.1.0",
        description=(
            "MVP local del Registro Institucional de Personas. "
            "La documentación describe el contrato; no habilita acceso. "
            "Las operaciones privadas permanecen bloqueadas hasta aprobar e integrar "
            "autenticación y autorización institucional."
        ),
    )

    @app.middleware("http")
    async def safe_request(request: Request, call_next):
        try:
            request.state.correlation_id = UUID(request.headers.get("X-Correlation-ID", ""))
        except ValueError:
            request.state.correlation_id = uuid4()
        try:
            response = await call_next(request)
        except Exception:
            # Never log exceptions, SQL, input, paths, subject, query strings, tokens or names.
            response = problem_response(request, 500, "internal-error", "Error interno")
        response.headers["X-Correlation-ID"] = str(request.state.correlation_id)
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request_completed correlation_id=%s status=%s",
            request.state.correlation_id,
            response.status_code,
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return problem_response(request, error.status, error.code, error.title)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # Pydantic error input/context may contain PII; don't return error.errors().
        return problem_response(request, 422, "validation-error", "Solicitud inválida")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        safe_headers = {}
        if error.headers and "WWW-Authenticate" in error.headers:
            safe_headers["WWW-Authenticate"] = error.headers["WWW-Authenticate"]
        return problem_response(
            request,
            error.status_code,
            "http-error",
            "Solicitud no disponible",
            safe_headers,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        return problem_response(request, 503, "database-unavailable", "Persistencia no disponible")

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok", "private_api": "requires_trusted_platform_delegation"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home():
        return HTMLResponse(
            """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Registro Institucional de Personas</title>
<style>
:root{color-scheme:light;--ink:#182432;--muted:#526171;--line:#d8e0e8;--blue:#075db5;--amber:#744400;--amber-bg:#fff4dc}
*{box-sizing:border-box}body{margin:0;background:#f3f6f9;color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:800px;margin:8vh auto;padding:24px}.card{background:white;border:1px solid var(--line);border-radius:16px;padding:clamp(24px,5vw,48px);box-shadow:0 12px 36px #18243212}
.eyebrow{color:var(--blue);font-size:.8rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase}h1{font-size:clamp(2rem,5vw,3rem);line-height:1.12;margin:.5rem 0}p{color:var(--muted)}.status{display:flex;align-items:center;gap:10px;font-weight:650;margin:24px 0}.dot{width:11px;height:11px;border-radius:50%;background:#15803d;box-shadow:0 0 0 4px #15803d1c}
.notice{background:var(--amber-bg);border-left:4px solid #d08a19;border-radius:8px;padding:16px 18px;margin:24px 0}.notice strong{color:var(--amber)}.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:28px}a{color:var(--blue);font-weight:650}.button{display:inline-block;background:var(--blue);color:white;padding:11px 17px;border-radius:8px;text-decoration:none}.button.secondary{background:white;color:var(--blue);border:1px solid var(--line)}footer{font-size:.88rem;margin-top:26px;color:var(--muted)}:focus-visible{outline:3px solid #efaa38;outline-offset:3px}
</style></head><body><main><section class="card" aria-labelledby="title">
<div class="eyebrow">Servicio institucional · Vista local</div><h1 id="title">Registro de Personas</h1>
<p>Identidad institucional, referencias de origen y vínculos de cuenta en un servicio independiente.</p>
<div class="status"><span class="dot" aria-hidden="true"></span><span>Proceso local en ejecución</span></div>
<aside class="notice" role="status"><strong>Acceso privado mediante OIDC institucional</strong>
<div>Las operaciones requieren un JWT delegado de corta duración, validado por firma, emisor, audiencia y permisos/fuentes. Sin confianza de plataforma configurada, el servicio deniega el acceso. Vincular cuentas continúa deshabilitado mientras falte una verificación de titularidad aprobada.</div></aside>
<div class="actions"><a class="button" href="/docs">Ver documentación de la API</a>
<a class="button secondary" href="/health/ready">Consultar disponibilidad</a></div>
<footer>La vista local no conecta con LDAP ni crea cuentas. El servicio de correo institucional opera por separado.</footer>
</section></main></body></html>""",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            },
        )

    @app.get("/health/ready", tags=["health"])
    def ready():
        try:
            _verifier()
            key_configured = True
        except (DomainError, OSError):
            key_configured = False
        if not key_configured:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "authentication_not_configured"},
            )
        if not database_is_ready():
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "database_unavailable"},
            )
        return {"status": "ready", "private_api": "platform_signed_delegation"}

    problem_responses = {
        status: {
            "description": "Problem Details sin datos de entrada",
            "content": {
                "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
            },
            "model": Problem,
        }
        for status in (403, 404, 409, 422, 500, 503)
    }
    private = {"x-access-control": "valid-oidc-operator-token-and-resource-authorization-required"}

    @app.post(
        "/v1/personas",
        response_model=PersonView,
        status_code=201,
        responses=problem_responses,
        openapi_extra=private,
    )
    def create_person(
        data: PersonCreate,
        service: RegistryDep,
        idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    ):
        return service.create(data, idempotency_key)

    @app.get(
        "/v1/personas",
        response_model=PersonPage,
        responses=problem_responses,
        openapi_extra=private,
    )
    def list_people(
        service: RegistryDep,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000),
    ):
        return service.list(limit, offset)

    @app.get(
        "/v1/personas/{person_id}",
        response_model=PersonView,
        responses=problem_responses,
        openapi_extra=private,
    )
    def get_person(person_id: UUID, service: RegistryDep):
        return service.get(person_id)

    @app.get(
        "/v1/personas/{person_id}/referencias",
        response_model=list[SourceReferenceView],
        responses=problem_responses,
        openapi_extra=private,
    )
    def person_references(person_id: UUID, service: RegistryDep):
        return service.references(person_id)

    @app.post(
        "/v1/personas/{person_id}/referencias",
        status_code=201,
        response_model=SourceReferenceView,
        responses={200: {"model": SourceReferenceView}, **problem_responses},
        openapi_extra=private,
    )
    def add_person_reference(
        person_id: UUID,
        data: SourceReferenceLink,
        response: Response,
        service: RegistryDep,
    ):
        reference, created = service.add_reference(person_id, data)
        response.status_code = 201 if created else 200
        return reference

    @app.get(
        "/v1/referencias/resolver",
        response_model=PersonView,
        responses=problem_responses,
        openapi_extra=private,
    )
    def resolve_person(service: RegistryDep, source: Annotated[SourceInput, Query()]):
        return service.resolve(source)

    @app.get(
        "/v1/revisiones",
        response_model=ReviewCasePage,
        responses=problem_responses,
        openapi_extra=private,
    )
    def list_reviews(
        service: RegistryDep,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000),
    ):
        return service.reviews(limit, offset)

    @app.post(
        "/v1/personas/{person_id}/cuentas",
        response_model=AccountView,
        status_code=201,
        responses={**problem_responses, 200: {"model": AccountView}},
        openapi_extra=private,
    )
    def link_account(
        person_id: UUID,
        data: AccountLink,
        service: RegistryDep,
        response: Response,
        verifier: Annotated[OwnershipVerifier, Depends(ownership_verifier)],
    ):
        account, created = service.link(person_id, data, verifier)
        response.status_code = 201 if created else 200
        return account

    def local_openapi():
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title, version=app.version, description=app.description, routes=app.routes
            )
            for path in schema["paths"].values():
                for operation in path.values():
                    if not isinstance(operation, dict):
                        continue
                    for code, response in operation.get("responses", {}).items():
                        if code.isdigit() and int(code) >= 400:
                            response.get("content", {}).pop("application/json", None)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = local_openapi
    return app


def problem_response(
    request: Request,
    status: int,
    code: str,
    title: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": f"urn:registro-personas:problem:{code}",
            "title": title,
            "status": status,
            "correlation_id": str(correlation(request)),
        },
        headers=headers,
    )


app = create_app()
