"""Resume slips, without a database: what one is bound to and how long."""

from __future__ import annotations

import uuid

from libvox_core import resume
from libvox_core.resume import ResumeStore

ORIGIN = "http://opac.exemplo"


def test_a_slip_opens_its_own_conversation() -> None:
    store = ResumeStore(ttl_s=60)
    conversation = uuid.uuid4()
    slip = store.issue("lib-a", ORIGIN, conversation)

    assert slip != str(conversation)
    assert str(conversation) not in slip
    assert store.redeem(slip, "lib-a", ORIGIN) == conversation


def test_another_library_or_another_origin_gets_nothing() -> None:
    store = ResumeStore(ttl_s=60)
    slip = store.issue("lib-a", ORIGIN, uuid.uuid4())

    assert store.redeem(slip, "lib-b", ORIGIN) is None
    assert store.redeem(slip, "lib-a", "http://outro.exemplo") is None
    assert store.redeem(slip, "lib-a", None) is None
    # A refused attempt does not burn it for the reader it belongs to.
    assert store.redeem(slip, "lib-a", ORIGIN) is not None


def test_an_idle_slip_expires_and_use_renews_it(monkeypatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr(resume.time, "time", lambda: now[0])
    store = ResumeStore(ttl_s=60)
    slip = store.issue("lib-a", ORIGIN, uuid.uuid4())

    now[0] += 50
    store.touch(slip)
    now[0] += 50  # 100s after issue, 50s after the last answer
    assert store.redeem(slip, "lib-a", ORIGIN) is not None

    now[0] += 61
    assert store.redeem(slip, "lib-a", ORIGIN) is None
    assert len(store) == 0


def test_a_revoked_slip_stops_at_once() -> None:
    store = ResumeStore(ttl_s=60)
    slip = store.issue("lib-a", ORIGIN, uuid.uuid4())

    assert store.revoke(slip) is True
    assert store.redeem(slip, "lib-a", ORIGIN) is None
    assert store.revoke(slip) is False


def test_expired_slips_do_not_accumulate(monkeypatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr(resume.time, "time", lambda: now[0])
    store = ResumeStore(ttl_s=60)
    for _ in range(resume._SWEEP_EVERY - 1):
        store.issue("lib-a", ORIGIN, uuid.uuid4())

    now[0] += 61
    store.issue("lib-a", ORIGIN, uuid.uuid4())  # the sweep runs here

    assert len(store) == 1
