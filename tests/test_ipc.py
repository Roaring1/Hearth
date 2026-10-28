"""IPC tests, centred on the bugs that actually happened.

The headline case is the oversized reply: the old fixed ``recv(256)``
truncated ``get_sinks`` JSON into something that would not parse.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from hearth.errors import IpcError
from hearth.ipc import client, protocol, server


@pytest.fixture
def sock_path(tmp_path):
    return tmp_path / "hearth.sock"


@pytest.fixture
def running(sock_path):
    """A started server whose handler is swappable per test."""
    state = {"handler": lambda request: f"echo:{request}"}
    srv = server.Server(lambda request: state["handler"](request), socket_path=sock_path)
    srv.start()
    try:
        yield srv, state
    finally:
        srv.stop()


def test_round_trip(running, sock_path):
    assert client.send("show", socket_path=sock_path) == "echo:show"


def test_reply_larger_than_the_old_256_byte_recv(running, sock_path):
    """The regression: a realistic get_sinks reply must survive intact."""
    payload = json.dumps(
        {f"vm_bus_{i}": {"volume": 55, "muted": False, "label": "x" * 40} for i in range(20)}
    )
    assert len(payload) > 256
    running[1]["handler"] = lambda _request: payload

    reply = client.send("get_sinks", socket_path=sock_path)
    assert reply == payload
    assert json.loads(reply)["vm_bus_19"]["volume"] == 55


def test_handler_exception_does_not_kill_the_listener(running, sock_path):
    """A raising handler used to break IPC permanently via `except: break`."""

    def boom(_request):
        raise RuntimeError("handler blew up")

    running[1]["handler"] = boom
    assert client.send("dump", socket_path=sock_path) in (None, "")

    running[1]["handler"] = lambda request: "still here"
    assert client.send("show", socket_path=sock_path) == "still here"


def test_handler_returning_none_replies_ok(running, sock_path):
    running[1]["handler"] = lambda _request: None
    assert client.send("unmute", socket_path=sock_path) == protocol.OK


def test_no_server_is_not_an_error(sock_path):
    assert client.send("show", socket_path=sock_path) is None
    assert client.is_running(socket_path=sock_path) is False


def test_stale_socket_file_is_detected_and_removed(sock_path):
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(str(sock_path))
    dead.close()  # file remains, nobody is listening

    assert sock_path.exists()
    assert client.send("show", socket_path=sock_path) is None
    assert client.clear_stale_socket(sock_path) is True
    assert not sock_path.exists()
    assert client.clear_stale_socket(sock_path) is False


def test_live_socket_is_never_removed(running, sock_path):
    assert client.clear_stale_socket(sock_path) is False
    assert sock_path.exists()


def test_stale_cleanup_preserves_a_live_socket_when_reply_is_slow(running, sock_path):
    def slow_handler(_request):
        time.sleep(0.75)
        return "still alive"

    running[1]["handler"] = slow_handler
    assert client.clear_stale_socket(sock_path) is False
    assert sock_path.exists()


def test_stale_cleanup_never_removes_a_regular_file(sock_path):
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.write_text("not a socket")

    assert client.clear_stale_socket(sock_path) is False
    assert sock_path.read_text() == "not a socket"


def test_bind_replaces_a_stale_unix_socket(sock_path):
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()  # filesystem entry remains, but no process is listening

    srv = server.Server(lambda _r: "ok", socket_path=sock_path)
    srv.start()
    try:
        assert client.send("show", socket_path=sock_path) == "ok"
    finally:
        srv.stop()


def test_bind_refuses_to_replace_a_regular_file(sock_path):
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.write_text("keep this file")
    srv = server.Server(lambda _r: "ok", socket_path=sock_path)

    with pytest.raises(FileExistsError, match="non-socket"):
        srv.bind()

    assert sock_path.read_text() == "keep this file"


def test_bind_does_not_unlink_a_live_server_socket(running, sock_path):
    active, _state = running
    original = sock_path.stat()
    competing = server.Server(lambda _r: "second", socket_path=sock_path)

    with pytest.raises(OSError, match="already has a listener"):
        competing.bind()

    current = sock_path.stat()
    assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
    assert client.send("show", socket_path=sock_path) == "echo:show"
    assert active.path.exists()


def test_listener_thread_start_failure_closes_socket_and_removes_owned_path(
    monkeypatch: pytest.MonkeyPatch, sock_path: Path
) -> None:
    srv = server.Server(lambda _r: "ok", socket_path=sock_path)

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(server.threading, "Thread", ThreadThatCannotStart)

    with pytest.raises(OSError, match="could not start the IPC listener thread"):
        srv.start()

    assert not sock_path.exists()
    assert srv._sock is None
    assert srv._owns_path is False
    assert srv._path_identity is None


def test_stop_does_not_unlink_a_replacement_path(sock_path):
    srv = server.Server(lambda _r: "unused", socket_path=sock_path)
    srv.bind()
    sock_path.unlink()
    sock_path.write_text("replacement file")

    srv.stop()

    assert sock_path.read_text() == "replacement file"


def test_stop_removes_the_socket_and_joins(sock_path):
    srv = server.Server(lambda _r: "ok", socket_path=sock_path)
    thread = srv.start()
    srv.stop()
    assert not thread.is_alive()
    assert not sock_path.exists()


def test_socket_permissions_are_owner_only(running, sock_path):
    assert sock_path.stat().st_mode & 0o777 == 0o600


def test_encode_rejects_embedded_newline():
    with pytest.raises(IpcError):
        protocol.encode("two\nlines")


def test_read_line_rejects_an_oversized_unterminated_frame():
    left, right = socket.socketpair()
    try:
        blob = b"x" * 9000  # no newline at all
        threading.Thread(target=lambda: (left.sendall(blob), left.close()), daemon=True).start()
        with pytest.raises(IpcError, match="exceeds 4096 bytes"):
            protocol.read_line(right, limit=4096)
    finally:
        right.close()


def test_read_line_limit_is_strict_even_for_non_aligned_limits():
    left, right = socket.socketpair()
    try:
        threading.Thread(
            target=lambda: (left.sendall(b"x" * 1200 + b"\n"), left.close()),
            daemon=True,
        ).start()
        with pytest.raises(IpcError, match="exceeds 1000 bytes"):
            protocol.read_line(right, limit=1000)
    finally:
        right.close()


def test_read_line_rejects_a_non_positive_limit():
    left, right = socket.socketpair()
    try:
        with pytest.raises(ValueError, match="limit must be positive"):
            protocol.read_line(right, limit=0)
    finally:
        left.close()
        right.close()


def test_read_line_reassembles_chunks():
    left, right = socket.socketpair()
    try:

        def writer():
            left.sendall(b"part-one ")
            left.sendall(b"part-two\n")
            left.close()

        threading.Thread(target=writer, daemon=True).start()
        assert protocol.read_line(right) == "part-one part-two"
    finally:
        right.close()


def test_is_known_filters_unsupported_commands():
    assert protocol.is_known("get_sinks")
    assert protocol.is_known("dump now")
    assert not protocol.is_known("rm -rf /")
    assert not protocol.is_known("")


def test_pid_helpers(tmp_path):
    pid_path = tmp_path / "hearth.pid"
    server.write_pid(pid_path)
    assert server.read_pid(pid_path) == os.getpid()
    assert server.pid_alive(server.read_pid(pid_path)) is True

    pid_path.write_text("not a number")
    assert server.read_pid(pid_path) is None
    assert server.pid_alive(None) is False
    assert server.pid_alive(0) is False
    # Not a hardcoded "surely impossible" number: this assertion started
    # failing when the machine's PID counter reached 3.9 million and 4000000
    # became a live process. Anything above pid_max cannot exist.
    pid_max_file = Path("/proc/sys/kernel/pid_max")
    pid_max = int(pid_max_file.read_text()) if pid_max_file.exists() else 32768
    assert server.pid_alive(pid_max + 1) is False


def test_write_pid_is_private_and_leaves_no_temporary_file(tmp_path):
    pid_path = tmp_path / "hearth.pid"

    server.write_pid(pid_path)

    assert server.read_pid(pid_path) == os.getpid()
    assert pid_path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob("hearth.pid.tmp.*")) == []


def test_write_pid_refuses_a_symlink_without_touching_its_target(tmp_path):
    pid_path = tmp_path / "hearth.pid"
    target = tmp_path / "keep.txt"
    target.write_text("not a pid")

    pid_path.symlink_to(target)

    with pytest.raises(OSError, match="non-regular PID path"):
        server.write_pid(pid_path)

    assert pid_path.is_symlink()
    assert target.read_text() == "not a pid"


def test_remove_pid_deletes_only_our_pid_file(tmp_path):
    pid_path = tmp_path / "hearth.pid"
    server.write_pid(pid_path)

    assert server.remove_pid(pid_path) is True
    assert not pid_path.exists()


def test_remove_pid_preserves_a_replaced_or_unrelated_pid(tmp_path):
    pid_path = tmp_path / "hearth.pid"
    pid_path.write_text("1234567" + chr(10))

    assert server.remove_pid(pid_path, expected_pid=os.getpid()) is False
    assert pid_path.read_text() == "1234567" + chr(10)


def test_remove_pid_does_not_unlink_a_symlink(tmp_path):
    pid_path = tmp_path / "hearth.pid"
    target = tmp_path / "keep.txt"
    target.write_text(str(os.getpid()))
    pid_path.symlink_to(target)

    assert server.remove_pid(pid_path) is False
    assert pid_path.is_symlink()
    assert target.exists()
