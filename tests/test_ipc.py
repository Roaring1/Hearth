"""IPC tests, centred on the bugs that actually happened.

The headline case is the oversized reply: the old fixed ``recv(256)``
truncated ``get_sinks`` JSON into something that would not parse.
"""

from __future__ import annotations

import json
import socket
import threading
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


def test_bind_replaces_a_leftover_socket_file(sock_path):
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.write_text("not a socket")
    srv = server.Server(lambda _r: "ok", socket_path=sock_path)
    srv.start()
    try:
        assert client.send("show", socket_path=sock_path) == "ok"
    finally:
        srv.stop()


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


def test_read_line_stops_at_the_limit():
    left, right = socket.socketpair()
    try:
        blob = b"x" * 9000  # no newline at all
        threading.Thread(target=lambda: (left.sendall(blob), left.close()), daemon=True).start()
        got = protocol.read_line(right, limit=4096)
    finally:
        right.close()
    assert 0 < len(got) <= 9000


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
    assert server.read_pid(pid_path) == __import__("os").getpid()
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
