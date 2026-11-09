"""Refusing to start unsafely, and never leaking a reason to the caller."""

from __future__ import annotations

import pytest

from libvox_core.config import ConfigurationError, Settings


def test_the_internal_api_refuses_to_start_without_a_service_token(monkeypatch) -> None:
    """The tenant arrives in the request body, so anything that can reach this
    port can read any library's data. A reverse proxy nobody configured is not
    a defence."""
    monkeypatch.setenv("LIBVOX_ENV", "production")
    monkeypatch.delenv("LIBVOX_CORE_SERVICE_TOKEN", raising=False)

    with pytest.raises(ConfigurationError, match="LIBVOX_CORE_SERVICE_TOKEN"):
        Settings.from_env()


def test_development_starts_without_one(monkeypatch) -> None:
    monkeypatch.setenv("LIBVOX_ENV", "development")
    monkeypatch.delenv("LIBVOX_CORE_SERVICE_TOKEN", raising=False)
    assert Settings.from_env().service_token is None


def test_it_binds_loopback_by_default(monkeypatch) -> None:
    """Not 0.0.0.0. Exposing this surface should be a decision, not a default."""
    monkeypatch.delenv("LIBVOX_CORE_HOST", raising=False)
    assert Settings.from_env().host == "127.0.0.1"


def test_the_port_does_not_collide_with_koha(monkeypatch) -> None:
    """8080 and 8081 are Koha Testing Docker's OPAC and staff interfaces, and a
    developer working on this project has one running."""
    monkeypatch.delenv("LIBVOX_CORE_PORT", raising=False)
    assert Settings.from_env().port == 8091


def test_a_missing_crypto_key_says_what_to_do(monkeypatch) -> None:
    settings = Settings(discovery_dsn="x", tenant_dsn="y", crypto_key=None)
    with pytest.raises(ConfigurationError, match="byte-identical"):
        settings.require_crypto_key()


def test_there_are_no_interactive_docs(settings) -> None:
    """This surface selects a tenant from its own body and must never be
    somewhere a browser goes looking."""
    from fastapi.testclient import TestClient

    from libvox_core.app import create_app

    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404


def test_health_answers_without_naming_a_dependency(settings) -> None:
    """A health check that reports which database is down is reconnaissance,
    and this one answers before any authentication."""
    from fastapi.testclient import TestClient

    from libvox_core.app import create_app

    with TestClient(create_app(settings)) as client:
        body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert not any(
        word in str(body).lower() for word in ("postgres", "dsn", "koha", "5433")
    )


def test_retention_reads_its_own_connection_string(monkeypatch) -> None:
    """Separate from the one the service writes through, because the service's
    role is subject to row-level security and would delete nothing."""
    monkeypatch.setenv("LIBVOX_CORE_SERVICE_TOKEN", "t" * 32)
    monkeypatch.setenv(
        "LIBVOX_CONVERSATION_ADMIN_DSN", "postgresql://o:o@127.0.0.1:9/conv"
    )
    settings = Settings.from_env()
    assert settings.conversation_admin_dsn == "postgresql://o:o@127.0.0.1:9/conv"
    assert settings.conversation_admin_dsn != settings.conversation_dsn


def test_the_timeouts_are_ordered_from_the_inside_out() -> None:
    """Each layer must be more patient than the one it waits on.

    This was inverted, and the failure it produced is the reason the rule is
    asserted rather than commented. Core waited five seconds on the tool layer
    -- httpx2's own default, because nothing set one -- while /libvox-mcp
    allowed sixty for the call it makes back, and the Koha driver allows thirty
    per request. A library in New Zealand answered an availability check in six
    seconds and Core had already stopped listening: the log carries `available
    (2 of 3 loanable)` and, six milliseconds later, "could not reach the tool
    layer".

    A reader was told the catalogue was unreachable about an answer that had
    arrived.
    """
    # Skipped rather than failed when /libvox-mcp is absent, which is what
    # pyproject.toml already says happens: it calls that module "a *test*
    # dependency ... and skips when it is absent". The import was bare, so it
    # failed instead -- and the two sibling-contract tests in test_endpoints.py
    # guard the same import correctly, which is where this spelling comes from.
    mcp_config = pytest.importorskip("libvox_mcp.config")
    McpSettings = mcp_config.Settings

    from libvox_driver_koha.client import DEFAULT_TIMEOUT_S as DRIVER

    core = Settings(
        discovery_dsn="postgresql://x", tenant_dsn="postgresql://x",
        crypto_key="k", environment="development",
    )
    mcp = McpSettings()

    assert DRIVER < mcp.core_timeout, "the tool layer gives up on a request the driver would still make"
    assert mcp.core_timeout < core.mcp_timeout_s, "Core gives up on work the tool layer is still doing"


def test_extra_body_refuses_malformed_json_rather_than_ignoring_it(monkeypatch) -> None:
    """A deployment that meant to turn thinking off and quietly did not would
    show up as a latency number nobody could explain, days later, in a table."""
    monkeypatch.setenv("LIBVOX_LLM_EXTRA_BODY", "{enable_thinking: false}")

    with pytest.raises(ConfigurationError, match="LIBVOX_LLM_EXTRA_BODY"):
        Settings.from_env()


def test_extra_body_must_be_an_object(monkeypatch) -> None:
    """It is merged into a JSON object, so a list or a string could only fail
    later, inside the request builder, with a message about neither."""
    monkeypatch.setenv("LIBVOX_LLM_EXTRA_BODY", '["enable_thinking"]')

    with pytest.raises(ConfigurationError, match="must be a JSON object"):
        Settings.from_env()


def test_no_extra_body_is_an_empty_dict_and_not_a_failure(monkeypatch) -> None:
    monkeypatch.delenv("LIBVOX_LLM_EXTRA_BODY", raising=False)

    assert Settings.from_env().llm_extra_body == {}
