"""Logging setup.

Two properties carry the weight here: nothing reaches stdout, and the log
destination does not depend on which agent launched the server.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import WatchedFileHandler
from pathlib import Path

import pytest

from libvox_mcp.logs import configure_logging, default_log_path, resolve_log_path


@pytest.fixture(autouse=True)
def _isolate_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """configure_logging uses force=True, which detaches pytest's handlers.

    Also points the default path inside tmp_path, so a test run never writes
    to the developer's real ~/.local/state.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("LIBVOX_LOG_FILE", raising=False)
    monkeypatch.delenv("LIBVOX_LOG_LEVEL", raising=False)

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        handler.close()
    root.handlers, root.level = saved_handlers, saved_level


def _handlers() -> list[logging.Handler]:
    return logging.getLogger().handlers


def _file_handlers() -> list[WatchedFileHandler]:
    return [h for h in _handlers() if isinstance(h, WatchedFileHandler)]


# --- the one rule ------------------------------------------------------------


def test_nothing_is_ever_written_to_stdout() -> None:
    """Under stdio transport stdout is the JSON-RPC channel. A log line there
    corrupts the stream and the host reports an unexplained protocol error."""
    configure_logging()

    streams = [h.stream for h in _handlers() if isinstance(h, logging.StreamHandler)]
    assert streams, "expected at least one stream handler"
    assert sys.stdout not in streams


def test_stderr_survives_alongside_the_file() -> None:
    """A host shows stderr when a subprocess dies at startup — which is the
    one failure a log file cannot record."""
    configure_logging()

    assert any(
        getattr(h, "stream", None) is sys.stderr
        for h in _handlers()
        if not isinstance(h, WatchedFileHandler)
    )


# --- the destination is the server's decision, not the agent's ---------------


def test_a_file_is_written_with_no_configuration_at_all() -> None:
    """The whole point: several agents drive this server, and under stdio the
    environment belongs to whichever one launched it. If the destination came
    from there, it would be a property of the agent."""
    configure_logging()

    assert len(_file_handlers()) == 1
    assert default_log_path().exists()


def test_the_default_honours_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "somewhere"))

    assert default_log_path() == tmp_path / "somewhere" / "libvox" / "mcp.log"


def test_without_xdg_state_home_it_falls_back_to_local_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    assert default_log_path() == Path.home() / ".local" / "state" / "libvox" / "mcp.log"


def test_two_agents_in_sequence_share_one_file(tmp_path: Path) -> None:
    """Different agents at different moments must accumulate in one place,
    and the second must not truncate the first's records."""
    configure_logging()
    logging.getLogger("libvox_mcp.test").warning("first agent")
    for handler in _handlers():
        handler.close()

    configure_logging()  # a second process, later
    logging.getLogger("libvox_mcp.test").warning("second agent")
    for handler in _handlers():
        handler.flush()

    written = default_log_path().read_text(encoding="utf-8")
    assert "first agent" in written
    assert "second agent" in written


def test_each_line_carries_the_pid() -> None:
    """Successive runs land in one file; without the pid they read as a single
    continuous session."""
    configure_logging()
    logging.getLogger("libvox_mcp.test").warning("koha refused the request")
    for handler in _handlers():
        handler.flush()

    written = default_log_path().read_text(encoding="utf-8")
    assert f"[pid {os.getpid()}]" in written
    assert "koha refused the request" in written


def test_the_destination_is_announced_at_startup(
    capsys: pytest.CaptureFixture,
) -> None:
    """Nobody has this path written down in an agent config — that is the whole
    point — so the server has to say where it is."""
    configure_logging()

    path = default_log_path()
    assert f"logging to {path}" in capsys.readouterr().err
    assert f"logging to {path}" in path.read_text(encoding="utf-8")


def test_nothing_is_announced_when_the_file_could_not_be_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Announcing a path that does not exist is worse than saying nothing."""
    blocked = tmp_path / "blocked"
    blocked.mkdir(mode=0o500)
    path = blocked / "sub" / "libvox.log"
    monkeypatch.setenv("LIBVOX_LOG_FILE", str(path))

    configure_logging()

    err = capsys.readouterr().err
    assert f"logging to {path}" not in err
    assert "cannot write to" in err


# --- the override ------------------------------------------------------------


def test_the_env_var_still_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator's exception, no longer the mechanism."""
    path = tmp_path / "elsewhere" / "custom.log"
    monkeypatch.setenv("LIBVOX_LOG_FILE", str(path))
    configure_logging()

    logging.getLogger("libvox_mcp.test").warning("hello")
    for handler in _handlers():
        handler.flush()

    assert path.read_text(encoding="utf-8").strip().endswith("hello")
    assert not default_log_path().exists()


def test_a_blank_override_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LIBVOX_LOG_FILE=` in a compose file is an unset variable, not a
    request for a file named ''."""
    monkeypatch.setenv("LIBVOX_LOG_FILE", "   ")

    assert resolve_log_path() == default_log_path()


# --- rotation is somebody else's job -----------------------------------------


def test_the_handler_reopens_after_an_external_rotation(tmp_path: Path) -> None:
    """Retention belongs to the Loki collector tailing this file. That only
    works if the server notices when the rotator moves the file aside."""
    configure_logging()
    path = default_log_path()

    logging.getLogger("libvox_mcp.test").warning("before rotation")
    for handler in _handlers():
        handler.flush()
    path.rename(tmp_path / "rotated.log")

    logging.getLogger("libvox_mcp.test").warning("after rotation")
    for handler in _handlers():
        handler.flush()

    assert path.exists(), "handler did not reopen the rotated-away path"
    assert "after rotation" in path.read_text(encoding="utf-8")


# --- failure -----------------------------------------------------------------


def test_an_unwritable_path_does_not_stop_the_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Logging is not the mission. But it is not silent either: believing you
    have a log file when you do not is how an incident gets investigated with
    no evidence."""
    blocked = tmp_path / "blocked"
    blocked.mkdir(mode=0o500)
    monkeypatch.setenv("LIBVOX_LOG_FILE", str(blocked / "sub" / "libvox.log"))

    configure_logging()  # must not raise

    assert not _file_handlers()
    assert "cannot write to" in capsys.readouterr().err


# --- level -------------------------------------------------------------------


def test_the_level_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBVOX_LOG_LEVEL", "debug")
    configure_logging()

    assert logging.getLogger().level == logging.DEBUG


def test_an_empty_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBVOX_LOG_LEVEL", "")
    configure_logging()

    assert logging.getLogger().level == logging.INFO
