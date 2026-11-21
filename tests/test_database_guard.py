import pytest

from .database_guard import validate_test_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://demo@remote.example.invalid/per001_test",
        "postgresql+psycopg://demo@127.0.0.1/other_database",
        "postgresql+psycopg://demo@127.0.0.1/per001_test?host=remote.example.invalid",
        "postgresql+psycopg://demo@127.0.0.1/per001_test?service=other",
        "postgresql+psycopg://demo@127.0.0.1/per001_test?options=-csearch_path=public",
        "sqlite:///per001_test",
        "",
    ],
)
def test_test_database_guard_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        validate_test_url(url)


def test_test_database_guard_accepts_explicit_local_fixture_database():
    url = "postgresql+psycopg://demo@127.0.0.1:55438/per001_test"
    assert validate_test_url(url) == url
