from sqlalchemy.engine import make_url


def validate_test_url(value: str) -> str:
    """Never echo URLs/credentials in failures; reject libpq host/service overrides."""
    if not value:
        raise ValueError("Set REGISTRY_TEST_DATABASE_URL to a dedicated local per001_test database")
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.database != "per001_test"
        or url.query
    ):
        raise ValueError("Tests require a loopback per001_test URL without query parameters")
    return value
