"""Propagation rules 3 and 7: the session credential.

The root README names these as this component's security acceptance criteria,
so they are asserted rather than described.
"""

from __future__ import annotations

import time

import pytest
from conftest import (
    SERVICE_TOKEN,
    TENANT_A,
    TENANT_B,
    databases_up,
    register_library,
)

from libvox_core.sessions import SessionStore

pytestmark = databases_up


# --- rule 7: opaque, short-lived, single-tenant, not re-scopable ------------


def test_a_credential_is_opaque_and_says_nothing_about_the_library() -> None:
    """A token that encoded its tenant would be a tenant the holder can read,
    and eventually one they can edit."""
    session = SessionStore().mint(TENANT_A)
    assert TENANT_A not in session.token
    assert "furg" not in session.token.lower()
    assert len(session.token) >= 40


def test_two_sessions_never_share_a_credential() -> None:
    store = SessionStore()
    tokens = {store.mint(TENANT_A).token for _ in range(50)}
    assert len(tokens) == 50


def test_a_session_cannot_be_re_scoped_to_another_library() -> None:
    """Not a slow path — an operation that does not exist.

    `Session` is frozen and the store has no setter, so moving a live
    credential to another library is impossible rather than merely discouraged.
    """
    store = SessionStore()
    session = store.mint(TENANT_A)

    with pytest.raises((AttributeError, TypeError)):
        session.tenant_id = TENANT_B  # type: ignore[misc]

    assert not any(
        hasattr(store, name) for name in ("retenant", "rebind", "update", "set_tenant")
    )
    assert store.resolve(session.token).tenant_id == TENANT_A


def test_an_expired_credential_stops_resolving() -> None:
    store = SessionStore(ttl_s=-1)
    session = store.mint(TENANT_A)
    assert store.resolve(session.token) is None


def test_expiry_is_answered_without_anything_sweeping() -> None:
    """Checked on read, so it is right whether or not a cleaner is running."""
    store = SessionStore(ttl_s=-1)
    store.mint(TENANT_A)
    assert len(store) == 1
    store.resolve(next(iter(store._sessions)))
    assert len(store) == 0


def test_a_revoked_credential_stops_at_once(client) -> None:
    """Not on the next expiry, and not when a cache decides to."""
    key = register_library(TENANT_A)
    token = client.post(
        "/internal/v1/sessions",
        json={"channel": "web", "binding_key": key, "origin": "http://localhost:8081"},
    ).json()["token"]

    assert client.post(
        "/internal/v1/sessions/introspect", json={"token": token}
    ).json()["active"]

    client.app.state.sessions.revoke(token)

    assert not client.post(
        "/internal/v1/sessions/introspect", json={"token": token}
    ).json()["active"]


def test_the_credential_carries_the_scope_the_mcp_layer_demands(client) -> None:
    """Without `catalog:read` every tool call is refused on the other side."""
    key = register_library(TENANT_A)
    body = client.post(
        "/internal/v1/sessions",
        json={"channel": "web", "binding_key": key, "origin": "http://localhost:8081"},
    ).json()

    introspected = client.post(
        "/internal/v1/sessions/introspect", json={"token": body["token"]}
    ).json()
    assert "catalog:read" in introspected["scopes"]


# --- rule 3: re-resolved every call, cached nowhere -------------------------


def test_the_tenant_is_re_resolved_on_every_call(client) -> None:
    """Two introspections of the same token both reach the store.

    A cache here would be the thing that lets a long conversation drift, and
    would make revocation take effect whenever the cache felt like it.
    """
    key = register_library(TENANT_A)
    token = client.post(
        "/internal/v1/sessions",
        json={"channel": "web", "binding_key": key, "origin": "http://localhost:8081"},
    ).json()["token"]

    store = client.app.state.sessions
    seen: list[str] = []
    original = store.resolve

    def counting(value: str):
        seen.append(value)
        return original(value)

    store.resolve = counting  # type: ignore[method-assign]
    for _ in range(3):
        client.post("/internal/v1/sessions/introspect", json={"token": token})
    assert len(seen) == 3


def test_an_unknown_token_is_inactive_and_not_an_error(client) -> None:
    """The client treats a non-200 as "Core is broken", so this must be 200."""
    response = client.post(
        "/internal/v1/sessions/introspect", json={"token": "nao-existe"}
    )
    assert response.status_code == 200
    assert response.json() == {"active": False}


def test_introspection_answers_without_a_service_token(settings, migrated) -> None:
    """It is the token verifier's own path.

    Gating it would turn a missing service token into "every caller is
    anonymous" instead of the startup failure it is.
    """
    from fastapi.testclient import TestClient

    from libvox_core.app import create_app

    with TestClient(create_app(settings)) as bare:
        response = bare.post(
            "/internal/v1/sessions/introspect", json={"token": "qualquer"}
        )
    assert response.status_code == 200


# --- how a session is born --------------------------------------------------


def test_a_widget_key_from_a_registered_origin_binds_a_session(client) -> None:
    key = register_library(TENANT_A)
    response = client.post(
        "/internal/v1/sessions",
        json={"channel": "web", "binding_key": key, "origin": "http://localhost:8081"},
    )
    body = response.json()
    assert body["tenant_id"] == TENANT_A
    assert body["expires_at"] > time.time()
    assert body["widget"]["assistant_name"] == "MARC.IA"


def test_the_credential_is_not_in_the_widget_configuration(client) -> None:
    """`SessionReadyEvent` carries config to the browser and never the token."""
    key = register_library(TENANT_A)
    body = client.post(
        "/internal/v1/sessions",
        json={"channel": "web", "binding_key": key, "origin": "http://localhost:8081"},
    ).json()
    assert body["token"] not in str(body["widget"])


@pytest.mark.parametrize(
    "case",
    [
        "unknown key",
        "disallowed origin",
        "suspended library",
        "channel switched off",
    ],
)
def test_every_refusal_is_the_same_answer(client, case: str) -> None:
    """Telling them apart would let a caller learn which one a given key is."""
    if case == "unknown key":
        register_library(TENANT_A)
        payload = {"binding_key": "wk_inventada", "origin": "http://localhost:8081"}
    elif case == "disallowed origin":
        key = register_library(TENANT_A)
        payload = {"binding_key": key, "origin": "https://outro-site.example"}
    elif case == "suspended library":
        key = register_library(TENANT_A, status="suspended")
        payload = {"binding_key": key, "origin": "http://localhost:8081"}
    else:
        key = register_library(TENANT_A, enabled=False, origins=[])
        payload = {"binding_key": key, "origin": "http://localhost:8081"}

    response = client.post("/internal/v1/sessions", json={"channel": "web", **payload})
    assert response.status_code == 200
    assert response.json() == {"active": False}


# --- the service token ------------------------------------------------------


def test_minting_needs_the_service_token(settings, migrated) -> None:
    from fastapi.testclient import TestClient

    from libvox_core.app import create_app

    register_library(TENANT_A)
    with TestClient(create_app(settings)) as bare:
        response = bare.post(
            "/internal/v1/sessions",
            json={"channel": "web", "binding_key": "wk_x", "origin": "x"},
        )
    assert response.status_code == 401


def test_a_wrong_service_token_is_refused(client) -> None:
    client.headers["x-libvox-service-token"] = SERVICE_TOKEN + "-errado"
    response = client.post(
        "/internal/v1/sessions",
        json={"channel": "web", "binding_key": "wk_x", "origin": "x"},
    )
    assert response.status_code == 401
