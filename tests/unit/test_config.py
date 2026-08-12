from pydantic import SecretStr

from mofiagent.config import Settings


def test_defaults_are_bounded() -> None:
    settings = Settings()

    assert settings.port == 8080
    assert settings.max_agent_rounds == 4
    assert settings.db_pool_max_size == 4
    assert settings.db_connect_timeout_seconds == 5
    assert settings.db_startup_timeout_seconds == 30
    assert settings.google_cloud_location == "us"
    assert settings.vertex_model == "gemini-3.5-flash"


def test_service_validation_rejects_missing_dependencies() -> None:
    settings = Settings()

    try:
        settings.validate_for_service()
    except ValueError as error:
        assert "MOFI_DATABASE_DSN" in str(error)
    else:
        raise AssertionError("expected service validation to fail")


def test_service_validation_accepts_complete_configuration() -> None:
    settings = Settings(
        database_dsn=SecretStr("postgresql://example"),
        google_cloud_project="example-project",
        vertex_model="example-model",
    )

    settings.validate_for_service()


def test_database_validation_rejects_inverted_pool_sizes() -> None:
    settings = Settings(
        database_dsn=SecretStr("postgresql://example"),
        db_pool_min_size=5,
        db_pool_max_size=4,
    )

    try:
        settings.validate_for_database()
    except ValueError as error:
        assert "cannot exceed" in str(error)
    else:
        raise AssertionError("expected database pool validation to fail")


def test_service_validation_requires_project() -> None:
    settings = Settings(database_dsn=SecretStr("postgresql://example"))

    try:
        settings.validate_for_service()
    except ValueError as error:
        assert "GOOGLE_CLOUD_PROJECT" in str(error)
    else:
        raise AssertionError("expected project validation to fail")
