import pytest

from config.settings.config import (
    InvalidConfigurationError,
    MissingConfigurationError,
    load_settings,
)


def test_load_settings_uses_defaults(monkeypatch):
    """Ensure defaults are used when optional variables are absent."""

    for key in [
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "MONGO_DB",
        "AIRFLOW__CORE__EXECUTOR",
        "AIRFLOW__CORE__LOAD_EXAMPLES",
        "LOG_LEVEL",
        "VALIDATION_STRICT_MODE",
        "VALIDATION_ENABLE_QUARANTINE",
    ]:
        monkeypatch.delenv(key, raising=False)

    # Required settings
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")

    settings = load_settings()

    assert settings.postgres_db == "etl_db"
    assert settings.postgres_user == "etl_user"
    assert settings.postgres_host == "postgres"
    assert settings.postgres_port == 5432

    assert settings.mongo_db == "etl_db"

    assert settings.airflow_executor == "LocalExecutor"
    assert settings.airflow_load_examples is False

    assert settings.log_level == "INFO"

    assert settings.validation_strict_mode is False
    assert settings.validation_enable_quarantine is True


def test_load_settings_uses_environment_values(monkeypatch):
    """Environment variables should override defaults."""

    monkeypatch.setenv("POSTGRES_DB", "appdb")
    monkeypatch.setenv("POSTGRES_USER", "appuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5433")

    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGO_DB", "appdb")

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = load_settings()

    assert settings.postgres_db == "appdb"
    assert settings.postgres_user == "appuser"
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5433

    assert settings.mongo_uri == "mongodb://localhost:27017"
    assert settings.mongo_db == "appdb"

    assert settings.log_level == "DEBUG"


def test_load_settings_rejects_invalid_boolean(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")

    monkeypatch.setenv(
        "AIRFLOW__CORE__LOAD_EXAMPLES",
        "maybe",
    )

    with pytest.raises(InvalidConfigurationError):
        load_settings()


def test_load_settings_rejects_invalid_integer(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")

    monkeypatch.setenv("POSTGRES_PORT", "abc")

    with pytest.raises(InvalidConfigurationError):
        load_settings()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_boolean_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")

    monkeypatch.setenv(
        "AIRFLOW__CORE__LOAD_EXAMPLES",
        value,
    )

    settings = load_settings()

    assert settings.airflow_load_examples is expected


def test_missing_required_postgres_password(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")

    with pytest.raises(MissingConfigurationError):
        load_settings()


def test_missing_required_mongo_uri(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.delenv("MONGO_URI", raising=False)

    with pytest.raises(MissingConfigurationError):
        load_settings()


def test_log_level_override(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    settings = load_settings()

    assert settings.log_level == "WARNING"