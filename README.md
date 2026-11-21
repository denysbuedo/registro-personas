# Registro Institucional de Personas · PER-001

MVP privado Python **3.12**, FastAPI, PostgreSQL, SQLAlchemy 2, Alembic,
Pydantic 2 y psycopg 3. Repositorio Git independiente en
`D:\TRABAJO\Desarrollo\registro-personas`; sin remoto, sin Docker.

Implementa UUID institucional inmutable, nombre mínimo con procedencia, referencias
únicas de origen, creación idempotente por actor/clave, consulta y paginación por
recursos autorizados, y vínculos `(issuer, subject)` verificados. Un operador con
`persons.read`, `persons.review` y permiso de fuente puede asociar una referencia
adicional a una persona seleccionada explícitamente. La procedencia de cada nueva
referencia se conserva; crear, repetir o detectar conflicto queda auditado. Los
conflictos devuelven 409 sin revelar otra identidad y guardan un caso restringido
PENDING; no se fusionan personas ni se reasignan referencias. Auditoría transaccional append-only.
`institutional_attributes` es solo estructura inicial con procedencia, sin API de
contribución ni catálogo aprobado. No hay contraseñas, tokens, LDAP ni clientes Keycloak.

## Instalación

Requiere Python 3.12 y PostgreSQL local (verificado aquí con PostgreSQL 18.4).
No se modifican servicios existentes. Desde este directorio en Ubuntu:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
```

En PowerShell usa `py -3.12 -m venv .venv` y `.venv/Scripts/python.exe` en
lugar de `python`; para Alembic usa `.venv/Scripts/alembic.exe`.
`requirements.lock` fija dependencias de ejecución y pruebas con hashes; no contiene
credenciales. La ejecución realizada utilizó Python 3.12.16 gestionado localmente en
`.tools` y un virtualenv `.venv`, ambos ignorados por Git.

## PostgreSQL local aislado

Para una instancia local de pruebas con datos ficticios, con `initdb`, `pg_ctl`, `createdb`
en PATH, estos comandos funcionan también en PowerShell (usar `.exe` si procede):

```bash
mkdir -p .local
initdb -D .local/pgdata -U per001_local --auth=trust --encoding=UTF8 --locale=C
pg_ctl -D .local/pgdata -l .local/postgresql.log -o "-h 127.0.0.1 -p 55438 -c log_statement=none -c log_min_error_statement=panic -c log_error_verbosity=terse" -w start
createdb -h 127.0.0.1 -p 55438 -U per001_local per001_dev
createdb -h 127.0.0.1 -p 55438 -U per001_local per001_test
```

PowerShell: `New-Item -ItemType Directory .local -Force` sustituye `mkdir -p`.
No repetir `initdb` ni `createdb` sobre una instancia de pruebas ya preparada.
`trust` es únicamente para este clúster desechable loopback, sin PII ni credenciales:
otros procesos locales pueden acceder. No es configuración de despliegue institucional.
Los datos y logs de esta instancia de pruebas quedan en `.local`, fuera de Git.

```bash
export REGISTRY_DATABASE_URL='postgresql+psycopg://per001_local@127.0.0.1:55438/per001_dev'
alembic upgrade head
alembic check
python -m registro_personas
```

PowerShell: `$env:REGISTRY_DATABASE_URL = 'postgresql+psycopg://per001_local@127.0.0.1:55438/per001_dev'`.
Arranque en `127.0.0.1:8000`, sin log de acceso que pueda registrar identificadores
de URL. `/health/live` es público y no consulta la BD. `/docs` y `/openapi.json`
documentan el contrato local provisional. Las rutas privadas exigen un token OIDC
RS256 y verifican firma, emisor, audiencia, vigencia y roles funcionales en el
backend. Sin configuración OIDC validada, deniegan antes de consultar PostgreSQL.
No se aceptan cabeceras de actor/permisos ni se crean cuentas locales.

Integración local con `plataforma-interoperabilidad`: la plataforma sirve su módulo
y proxy de operador en `127.0.0.1:8003`; `tools/run_persons.py` arranca este servicio
independiente en `127.0.0.1:8013`. El proxy reenvía el mismo Bearer humano y ambos
backends validan su firma/issuer/audience/exp y permisos. El lanzador elimina la
configuración OIDC y de base heredada, por lo que el servicio queda cerrado por
defecto. No hay segunda base ni lectura cruzada de tablas. El correo permanece
independiente y sin cambios.

Configura solo en desarrollo o en un entorno institucional autorizado `REGISTRY_DELEGATION_ISSUER`,
`REGISTRY_DELEGATION_AUDIENCE=registro-personas`,
`REGISTRY_DELEGATION_KEY_ID` y una de
`REGISTRY_DELEGATION_PUBLIC_KEY_FILE` (preferida, clave pública RS256 fijada) o
`REGISTRY_DELEGATION_JWKS_URL`, además de `REGISTRY_DATABASE_URL`. Registro solo
acepta JWT delegado por la plataforma, nunca el bearer OIDC del navegador.
La plataforma usa `PERSONS_OIDC_ISSUER_URL`,
`PERSONS_OIDC_CLIENT_ID=interoperabilidad-admin` y
`PERSONS_OIDC_REDIRECT_URI` explícitos; la callback local autorizada provisional es
`http://127.0.0.1:8003/admin/personas`. Para una despliegue institucional bajo
HTTPS, la URI exacta debe asignarse y autorizarse antes de configurar Keycloak.
La autorización funcional la mantiene la plataforma en un archivo JSON externo
configurado mediante `PERSONS_AUTHORIZATION_POLICY_FILE`, con permisos y fuentes
asociados al par OIDC validado `issuer`/`subject`. No copiar subjects de producción
ni permisos funcionales a roles de Keycloak; sin una política legible, la API deniega.
El Registro no debe recibir ni leer ese archivo.
El token delegado se firma con RS256 desde `PERSONS_DELEGATION_PRIVATE_KEY_FILE`
(protegido fuera del repositorio), valida emisor/audiencia, `kid`, permisos/fuentes
y vida máxima de 120 segundos (60 por defecto). Los roles funcionales se mantienen
en la plataforma: `persons.read`, `persons.register`, `persons.review`, `persons.link`
y `persons.source.<alias>`; este último conjunto incluye los alias iniciales
`reduniv.conecta`, `reduniv.aprende` y `reduniv.convoca`. La tabla de auditoría conserva
issuer/subject del operador con acceso restringido; la aplicación y el proxy no
registran esos valores crudos ni los devuelven por API. Las denegaciones registran
solo el `actor_id` pseudónimo, el permiso y la correlación de la solicitud.

No ejecutar Uvicorn con su access log predeterminado, ni habilitar SQL echo,
trazas de parámetros, logs de cuerpos/query strings o logs SQL de servidor con PII.
La aplicación registra solo código HTTP y UUID de correlación; no reproduce entradas
ni detalles de SQL/excepciones en respuestas RFC 9457.

## Pruebas funcionales con fixtures ficticios

```bash
export REGISTRY_TEST_DATABASE_URL='postgresql+psycopg://per001_local@127.0.0.1:55438/per001_test'
python -m pytest -q
ruff check src tests migrations
ruff format --check src tests migrations
```

La prueba de integración entre ambos repositorios se activa expresamente en el
entorno local (usa tokens RSA ficticios y la base aislada `per001_test`; no conecta
con Keycloak):

```powershell
$env:REGISTRY_TEST_DATABASE_URL = 'postgresql+psycopg://per001_local@127.0.0.1:55438/per001_test'
$env:PER002_PLATFORM_ROOT = 'D:\01.DEVOP\Proyectos\plataforma-interoperabilidad'
.venv/Scripts/python.exe -m pip install -e "${env:PER002_PLATFORM_ROOT}[dev]"
.venv/Scripts/python.exe -m pytest -q
```

PowerShell: `$env:REGISTRY_TEST_DATABASE_URL = 'postgresql+psycopg://per001_local@127.0.0.1:55438/per001_test'`;
después `.venv/Scripts/python.exe -m pytest -q`.
La suite rechaza hosts no loopback, bases distintas de `per001_test` y parámetros
query que puedan alterar libpq. Solo crea/elimina esquemas UUID temporales propios;
no usa SQLite ni una base de otra aplicación. Si falta la base, las pruebas de
integración fallan expresamente, sin simular éxito.

Demostración completa de API sin abrir escrituras en red:
`python -m pytest tests/test_api.py::test_api_create_query_link_and_generic_conflict -q`.
Los adaptadores están **solo en tests**: `tests/fakes.py` verifica fixtures de
titularidad ligando actor, persona, issuer, subject y referencias de evidencia.
No es autenticación de desarrollo instalable ni un patrón M2M.

## Contrato y extensión institucional pendiente

[docs/openapi.yaml](docs/openapi.yaml) define cinco operaciones privadas provisionales:
crear persona, obtener/listar autorizadas, resolver referencia y vincular cuenta.
El esquema ejecutable es `/openapi.json`; las pruebas contrastan rutas y modelos.
Los nombres locales snake_case y estas operaciones **no ratifican ni modifican** el
borrador de Personas 0.1.0 del repositorio de gobierno. No se implementa su flujo
`clientCredentials`. Permisos locales son puntos de extensión, no scopes OAuth aprobados.

`security.authenticate` debe sustituirse únicamente mediante integración revisada:
un adaptador confiable entrega `Principal` con actor opaco y recursos/sistemas
autorizados. No basta validar autenticación: backend comprueba permisos de operación,
source y persona. Listar exige `persons.read` y `persons.list`, y filtra antes de
contar/paginar. `OwnershipVerifier` debe consultar evidencia institucional confiable;
`verification_ref` en el body no demuestra titularidad. Su implementación actual deniega.
No existe endpoint de reasignación ni resolución automática de casos de revisión.

Antes de desplegar: aprobar mecanismo M2M, ownership/ciclo de `person_id`, reglas
de vinculación, retención de auditoría/idempotencia y atributos autorizados. El runtime
de BD debe tener permisos mínimos SELECT/INSERT y los UPDATE necesarios, **sin ser
propietario, sin DDL/TRUNCATE ni DELETE/UPDATE de auditoría**; usar rol separado para
migraciones. Los triggers no protegen frente a un administrador/propietario de BD.
El ejemplo [systemd](deploy/registro-personas.service) no se instala ni activa aquí.

Referencia de gobierno: repositorio vecino `arquitectura_institucional`, encargo
PER-001; ADR-0002 aprobado y ADR-0001 propuesto. El stack fue aprobado expresamente
por el orquestador en esta sesión; ninguna decisión de gobierno fue modificada.
Las dependencias reales de IAM siguen pendientes; no bloquean pruebas internas.

## Reversión local

Con el proceso detenido: `alembic downgrade base` retira **todas las tablas y datos del
MVP** en la base configurada; verificar destino y respaldar antes. `alembic upgrade head`
las recrea vacías. La suite verifica upgrade/check/downgrade/re-upgrade en esquema
aislado. No hay rollback productivo aprobado. Para detener el clúster local de pruebas:
`pg_ctl -D .local/pgdata -m fast -w stop`. No se borran automáticamente sus archivos.

Referencias técnicas utilizadas: [SQLAlchemy PostgreSQL](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)
y [FastAPI dependency overrides](https://fastapi.tiangolo.com/advanced/testing-dependencies/).
