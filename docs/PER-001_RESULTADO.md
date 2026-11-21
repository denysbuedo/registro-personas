# PER-001 · Resultado para el orquestador

Fecha: 2026-11-19. Código en `D:\TRABAJO\Desarrollo\registro-personas`, repositorio
Git local independiente, rama `main`, sin remoto ni publicación. Stack aprobado en
la sesión: Python 3.12/FastAPI/PostgreSQL/SQLAlchemy 2/Alembic/Pydantic 2/psycopg 3.

**Funciona:** creación con UUID inmutable e idempotencia por actor/clave; referencias
de origen únicas; lectura/resolución y paginación filtradas por recursos autorizados;
vínculos de cuentas con evidencia de titularidad y sin reasignación; conflictos
genéricos con casos PENDING restringidos; procedencia de nombres y estructura inicial
de atributos; auditoría transaccional con referencia opaca de verificación; errores
RFC 9457 y correlación sin datos de entrada. Contrato OpenAPI local provisional.

**Verificado realmente:** Python 3.12.16 y PostgreSQL 18.4 locales, sin Docker.
`python -m pytest -q`: **42 passed, 1 warning, 2.13 s**. Incluye concurrencia real
en creación/vinculación, no fusión por nombre, conflictos sin huérfanos, permisos,
titularidad, errores/logs saneados, inmutabilidad SQL, atributos con procedencia,
contraste OpenAPI y guardas de BD de pruebas. Warning: Starlette depreca su integración
de TestClient con httpx; no hay prueba omitida ni fallida.
Alembic: upgrade/check/downgrade/re-upgrade reales en BD local, `head` confirmado y
sin diferencias de metadatos; la suite repite el ciclo en esquemas temporales propios.
Ruff check y format --check correctos. Smoke HTTP real: health 200, OpenAPI 200,
consulta privada 503 antes de BD y sin identificador de URL en logs; servidor detenido.
Revisión independiente: 33 pruebas disponibles entonces correctas; sus observaciones
sobre permisos, logs, referencias de revisión y guardas de conexión quedaron incorporadas.

**Archivos:** `src/registro_personas/` (modelos, schemas, servicio, API y puertos de
seguridad), `migrations/` y `alembic.ini`, `tests/`, `pyproject.toml`, lock de dependencias
con hashes, `docs/openapi.yaml`, `README.md` y ejemplo systemd no instalado.
Los documentos/ADR/contratos del repositorio de gobierno no se modificaron.

**Límites reales:** todas las operaciones privadas devuelven 503 por defecto; no hay
autenticación/M2M institucional aprobada. Dobles de identidad/titularidad solo en tests,
sin mecanismo de bypass por red. La tabla de atributos no habilita contribuciones ni
Perfil 360. No hay API de resolución de casos, fusión, baja o reasignación. Pendientes
ownership y retención institucionales, roles mínimos de BD y validación en Ubuntu;
las pruebas se ejecutaron en Windows. No hubo conexión productiva ni cambios a IAM.

**Siguiente integración mínima:** encargo separado para adaptador de autenticación,
autorización por recurso y evidencia de titularidad aprobados, coordinando el contrato
provisional con sus consumidores; después probar con datos ficticios antes de habilitar
red. No habilitar clientCredentials/service accounts para resolver este pendiente.
Arranque y pruebas reproducibles en [README](../README.md).

**Rollback:** migración `downgrade base` elimina datos del MVP únicamente en una BD
local verificada y respaldada; no es plan productivo. El clúster ficticio de esta
entrega se deja detenido, con datos ignorados por Git bajo `.local` y sin borrar archivos.
