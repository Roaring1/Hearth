"""The login1 inhibitor adapter is inert until Start and owns only a returned FD."""

from __future__ import annotations

import errno
import os
from typing import Any

import pytest

from hearth.ui.gtk4.capture_inhibit import (
    CaptureIdleInhibitorError,
    Login1IdleInhibitorAdapter,
)


class FakeVariant:
    def __init__(self, signature: str, value: tuple[Any, ...]) -> None:
        self.signature = signature
        self.value = value

    def unpack(self) -> tuple[Any, ...]:
        return self.value


class FakeVariantType:
    @staticmethod
    def new(signature: str) -> str:
        return signature


class FakeFDList:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.indices: list[int] = []

    def get(self, index: int) -> int:
        self.indices.append(index)
        return os.dup(self.fd)


class FakeConnection:
    def __init__(self, fd: int) -> None:
        self.fd_list = FakeFDList(fd)
        self.calls: list[tuple[Any, ...]] = []
        self.closed = False
        self.fail_call: Exception | None = None
        self.close_result = True

    def call_with_unix_fd_list_sync(self, *args: Any) -> tuple[FakeVariant, FakeFDList]:
        self.calls.append(args)
        if self.fail_call is not None:
            raise self.fail_call
        return FakeVariant("(h)", (0,)), self.fd_list

    def close_sync(self, _cancellable: object) -> bool:
        self.closed = True
        return self.close_result


class FakeGio:
    class BusType:
        SYSTEM = "system"

    class DBusConnectionFlags:
        AUTHENTICATION_CLIENT = 1
        MESSAGE_BUS_CONNECTION = 8

    class DBusCallFlags:
        NONE = 0

    class DBusConnection:
        @staticmethod
        def new_for_address_sync(
            address: str, flags: int, observer: object, cancellable: object
        ) -> FakeConnection:
            raise AssertionError("the injected factory should be used")

    @staticmethod
    def dbus_address_get_for_bus_sync(bus_type: str, cancellable: object) -> str:
        raise AssertionError("the injected factory should be used")


class FakeGLib:
    Variant = FakeVariant
    VariantType = FakeVariantType


def _pipe() -> tuple[int, int]:
    return os.pipe()


def test_construction_is_inert_and_acquire_uses_one_bounded_login1_call() -> None:
    read_fd, write_fd = _pipe()
    connection = FakeConnection(read_fd)
    factories: list[str] = []
    adapter = Login1IdleInhibitorAdapter(
        timeout_ms=2500,
        connection_factory=lambda: factories.append("open") or connection,
        gio=FakeGio,
        glib=FakeGLib,
    )

    assert factories == []
    lease = adapter.acquire()

    assert factories == ["open"]
    assert connection.closed
    assert len(connection.calls) == 1
    args = connection.calls[0]
    assert args[:4] == (
        "org.freedesktop.login1",
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
        "Inhibit",
    )
    assert args[4].signature == "(ssss)"
    assert args[4].value == (
        "idle:sleep",
        "Hearth",
        "Capture session is active",
        "block",
    )
    assert args[5] == "(h)"
    assert args[6] == FakeGio.DBusCallFlags.NONE
    assert args[7] == 2500
    assert args[8:] == (None, None)
    assert connection.fd_list.indices == [0]
    assert lease.active
    lease_fd = lease._fd
    assert lease_fd is not None
    assert os.get_inheritable(lease_fd) is False
    os.fstat(lease_fd)

    lease.release()
    assert not lease.active
    with pytest.raises(OSError):
        os.fstat(lease_fd)
    lease.release()  # Idempotent Stop/retry after successful release.
    os.close(read_fd)
    os.close(write_fd)


def test_call_failure_closes_private_connection_and_fails_closed() -> None:
    read_fd, write_fd = _pipe()
    connection = FakeConnection(read_fd)
    connection.fail_call = TimeoutError("login1 call timed out")
    adapter = Login1IdleInhibitorAdapter(
        connection_factory=lambda: connection, gio=FakeGio, glib=FakeGLib
    )

    with pytest.raises(CaptureIdleInhibitorError, match="login1 call timed out"):
        adapter.acquire()

    assert connection.closed
    os.fstat(read_fd)
    os.close(read_fd)
    os.close(write_fd)


def test_invalid_returned_fd_is_rejected_and_connection_is_closed() -> None:
    read_fd, write_fd = _pipe()
    connection = FakeConnection(read_fd)
    connection.fd_list.get = lambda _index: -1  # type: ignore[method-assign]
    adapter = Login1IdleInhibitorAdapter(
        connection_factory=lambda: connection, gio=FakeGio, glib=FakeGLib
    )

    with pytest.raises(CaptureIdleInhibitorError, match="invalid inhibitor file descriptor"):
        adapter.acquire()

    assert connection.closed
    os.close(read_fd)
    os.close(write_fd)


def test_private_bus_open_uses_system_bus_and_authenticates_as_a_message_bus() -> None:
    read_fd, write_fd = _pipe()
    connection = FakeConnection(read_fd)
    calls: list[tuple[Any, ...]] = []

    class PrivateGio(FakeGio):
        @staticmethod
        def dbus_address_get_for_bus_sync(bus_type: str, cancellable: object) -> str:
            calls.append(("address", bus_type, cancellable))
            return "unix:path=/run/dbus/system_bus_socket"

        class DBusConnection:
            @staticmethod
            def new_for_address_sync(
                address: str, flags: int, observer: object, cancellable: object
            ) -> FakeConnection:
                calls.append(("connection", address, flags, observer, cancellable))
                return connection

    adapter = Login1IdleInhibitorAdapter(gio=PrivateGio, glib=FakeGLib)
    lease = adapter.acquire()

    assert calls == [
        ("address", "system", None),
        ("connection", "unix:path=/run/dbus/system_bus_socket", 9, None, None),
    ]
    assert connection.closed
    lease.release()
    os.close(read_fd)
    os.close(write_fd)


def test_timeout_bounds_are_validated_without_connecting_to_dbus() -> None:
    for timeout in (0, 99, 10001):
        with pytest.raises(ValueError, match="between 100 and 10000"):
            Login1IdleInhibitorAdapter(timeout_ms=timeout)


def test_release_error_is_reported_without_retrying_a_reused_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = _pipe()
    connection = FakeConnection(read_fd)
    adapter = Login1IdleInhibitorAdapter(
        connection_factory=lambda: connection, gio=FakeGio, glib=FakeGLib
    )
    lease = adapter.acquire()
    lease_fd = lease._fd
    assert lease_fd is not None
    real_close = os.close
    attempts = 0

    def close_then_report_error(fd: int) -> None:
        nonlocal attempts
        attempts += 1
        real_close(fd)  # Linux releases the descriptor before reporting EIO.
        raise OSError(errno.EIO, "close reported an I/O error")

    monkeypatch.setattr(os, "close", close_then_report_error)
    with pytest.raises(CaptureIdleInhibitorError, match="close reported an I/O error"):
        lease.release()
    assert not lease.active
    lease.release()  # Idempotent; never closes the same descriptor number again.
    assert attempts == 1
    with pytest.raises(OSError):
        os.fstat(lease_fd)
    real_close(read_fd)
    real_close(write_fd)
