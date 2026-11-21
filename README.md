# Registro Institucional de Personas

Servicio independiente que asigna y mantiene el identificador institucional de cada persona. La aplicación propietaria de los datos es este servicio: los consumidores acceden a sus operaciones por API y no leen su base de datos.

## Qué incluye

- Identificador institucional inmutable person_id, de tipo UUID.
- Registro de personas con datos mínimos y procedencia.
- Referencias a registros de sistemas institucionales de origen, únicas por sistema, tipo de entidad e identificador externo.
- Consulta por person_id o por referencia exacta; no busca ni fusiona por nombre o correo.
- Vinculación de referencias de origen a una persona seleccionada explícitamente.
- Registro transaccional de operaciones, repeticiones y conflictos.
- Modelo inicial para atributos institucionales con procedencia. No hay todavía catálogo aprobado ni API general para mantener esos atributos.

Una referencia de origen identifica el registro de la persona en otro sistema; no es el person_id y no se genera en este servicio. Un conflicto de referencia devuelve HTTP 409 y requiere revisión. El servicio no reasigna referencias ni fusiona personas automáticamente.

## Arquitectura y seguridad

Implementado con Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, PostgreSQL, Alembic y psycopg 3. La API se documenta en docs/openapi.yaml y en el OpenAPI generado por la aplicación.

La plataforma de interoperabilidad autentica al operador mediante OIDC y aplica los permisos funcionales. Para invocar el Registro, la plataforma firma una delegación RS256 de corta duración con permisos y fuentes autorizadas. El Registro valida firma, emisor, audiencia, vigencia y claims. No confía en roles enviados por el navegador ni crea cuentas locales.

El sistema no almacena contraseñas, no crea usuarios en Keycloak, no importa usuarios LDAP, no escribe en LDAP y no usa client_credentials. No conecta directamente con bases de datos de consumidores.

El vínculo entre una cuenta digital y una persona requiere comprobar la titularidad. El verificador de titularidad incluido deniega esa operación hasta que exista un mecanismo institucional aprobado e integrado. No usar esa ruta para vincular cuentas reales.

## Estado de habilitación

El servicio está instalado para validación privada, pero todavía no debe recibir datos reales. Las operaciones privadas permanecen cerradas hasta que se configure PostgreSQL y se complete la validación autenticada de extremo a extremo con la plataforma.

- GET /health/live confirma que el proceso responde; no comprueba la base de datos.
- GET /health/ready devuelve 503 si falta la configuración de delegación o la URL de base de datos. Que devuelva 200 confirma la presencia de esa configuración, no sustituye una prueba de conexión y migraciones.
- Las rutas /v1 requieren un token delegado válido; sin autenticación responden 401. Sin configuración confiable, el servicio permanece no disponible.
- El servicio debe permanecer en loopback o en una red privada accesible solo por el consumidor autorizado. No publicar directamente sus operaciones privadas.

La instalación del servicio no equivale a habilitar el flujo institucional. Antes de datos reales deben aprobarse la base y sus permisos, la configuración OIDC/delegación, los permisos por fuente, la auditoría y la reversión de cambios.

## Requisitos

- Python 3.12.
- PostgreSQL para persistencia y migraciones.
- En Ubuntu Server, herramientas de compilación que puedan requerir las dependencias Python instaladas.
- No requiere Docker.

## Instalación local

Desde la raíz del repositorio:

~~~bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
~~~

En Windows PowerShell:

~~~powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.lock
.venv/Scripts/python.exe -m pip install --no-deps -e .
~~~

## Base local de desarrollo

Usar una base local desechable, exclusivamente con datos ficticios. No usar una base de producción o de otra aplicación.

Ejemplo con PostgreSQL local ya instalado:

~~~bash
createdb registro_personas_dev
export REGISTRY_DATABASE_URL='postgresql+psycopg://usuario_local:clave_local@127.0.0.1:5432/registro_personas_dev'
alembic upgrade head
alembic check
~~~

Guarda las credenciales locales fuera del repositorio. El archivo .env está excluido por .gitignore; no subas claves privadas, contraseñas ni archivos de entorno.

## Arranque local

Con la base local y la configuración de autenticación no disponible, el proceso puede arrancar para inspección, pero las operaciones privadas permanecen cerradas:

~~~bash
python -m registro_personas
~~~

Escucha en 127.0.0.1:8000 por defecto. Se puede cambiar el puerto con --port. La aplicación deshabilita el access log de Uvicorn para evitar que referencias de origen queden en rutas o query strings.

- OpenAPI JSON: /openapi.json
- Documentación: /docs
- Liveness: /health/live
- Readiness: /health/ready

La especificación de /docs describe el contrato; no concede acceso ni significa que las operaciones estén habilitadas.

## Pruebas locales

La suite usa la base aislada registro_personas_test y fixtures ficticios. Nunca apuntar REGISTRY_TEST_DATABASE_URL a una base institucional o productiva.

~~~bash
export REGISTRY_TEST_DATABASE_URL='postgresql+psycopg://usuario_local:clave_local@127.0.0.1:5432/registro_personas_test'
python -m pytest -q
ruff check src tests migrations
ruff format --check src tests migrations
~~~

Las pruebas revisan API, autorización, conflictos de referencias, migraciones, límites de conexión y composición con la plataforma. La integración local no contacta Keycloak ni LDAP.

## Modelo de autorización delegado

La plataforma determina la identidad autenticada y los permisos funcionales. El token delegado debe incluir una audiencia para este servicio, emisor confiable, subject y emisor originales del operador, permisos, fuentes, identificador de token y vigencia corta. El servicio valida esos elementos con una clave pública fijada o un JWKS HTTPS aprobado.

Variables de configuración del servicio:

- REGISTRY_DATABASE_URL
- REGISTRY_DELEGATION_ISSUER
- REGISTRY_DELEGATION_AUDIENCE
- REGISTRY_DELEGATION_KEY_ID
- REGISTRY_DELEGATION_PUBLIC_KEY_FILE o REGISTRY_DELEGATION_JWKS_URL

Los valores reales se provisionan fuera de Git y mediante el mecanismo institucional autorizado. La plataforma conserva su política de permisos y fuentes; el Registro vuelve a comprobar los permisos delegados por operación y recurso. No incluir tokens, subjects personales, datos de personas ni credenciales en logs o documentación.

## Despliegue

deploy/registro-personas.service es una plantilla de unidad systemd, no una unidad lista para copiar sin revisión. El equipo responsable del servidor debe definir el usuario del servicio, directorios, archivo de entorno protegido, base, copias, permisos, proxy/red privada, monitorización y reversión para cada entorno. No almacenar secretos en la unidad ni en el repositorio.

El proceso de migración debe ejecutarse con un rol separado del usuario runtime. El usuario runtime debe tener solo los permisos de datos necesarios, sin DDL, TRUNCATE ni borrado de auditoría. Probar migraciones y restauración antes de habilitar operaciones con datos reales.

No se modifica infraestructura automáticamente al instalar el paquete. No activar públicamente el servicio ni habilitar escrituras hasta que la autenticación, permisos, base, auditoría y reversión hayan sido comprobados de extremo a extremo.

## Reglas de integración

- Las aplicaciones consumidoras envían sus referencias de origen por la plataforma autorizada.
- La referencia usa source_system, source_entity_type y source_record_id.
- Una persona puede tener referencias distintas en varios sistemas.
- Una referencia repetida no crea una segunda persona y una referencia que pertenece a otra persona produce conflicto para revisión.
- person_id es la referencia institucional; username, correo y DN LDAP no lo sustituyen.
- El Registro no aprovisiona cuentas ni sincroniza datos desde LDAP.
- El servicio de correo existente es independiente y no forma parte de este repositorio.

## Estructura

- src/registro_personas/: API, seguridad, modelos y lógica del servicio.
- migrations/: migraciones Alembic.
- tests/: pruebas automatizadas.
- docs/openapi.yaml: contrato provisional de la API.
- deploy/: plantilla systemd para revisión por el equipo de despliegue.
- requirements.lock: dependencias fijadas con hashes.