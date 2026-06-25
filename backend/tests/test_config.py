"""Settings tests: database URL assembly and password URL-encoding."""

from src.config import Settings


def test_explicit_database_url_is_used_verbatim():
    url = "postgresql+asyncpg://u:p@h:5432/d"
    assert Settings(database_url=url).database_url == url


def test_assembled_from_parts():
    s = Settings(
        database_url="", db_host="pg", db_port=5432,
        db_user="scryme", db_password="secret", db_name="scryme",
    )
    assert s.database_url == "postgresql+asyncpg://scryme:secret@pg:5432/scryme"


def test_password_with_special_chars_is_encoded():
    # A password full of URL-reserved characters must not corrupt the host portion.
    s = Settings(database_url="", db_host="pg", db_password="W9@uVZ:F/!x")
    assert s.database_url.count("@") == 1          # only the user:pass@host delimiter
    assert "@pg:5432/" in s.database_url           # host/port/db intact
    assert "%40" in s.database_url                 # @ encoded
    assert "%3A" in s.database_url                 # : encoded
    assert "%2F" in s.database_url                 # / encoded
