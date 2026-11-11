"""On-demand systemd-logind idle/sleep inhibition for active Capture Mode.

This GTK-layer adapter deliberately uses Gio only when ``acquire`` is called.
It opens a private system-bus connection, obtains logind's Unix-FD inhibitor,
then closes the D-Bus connection while retaining only the returned FD. The
kernel closes that FD if Hearth exits, so no helper process, daemon, timer, or
capture-specific bus watcher survives a session.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402 - versions must be required first

from hearth.errors import HearthError  # noqa: E402


class CaptureIdleInhibitorError(HearthError):
    """The system idle/sleep inhibitor could not be acquired or released."""


class Login1IdleInhibitorLease:
    """A logind inhibitor held solely by an open Unix file descriptor."""

    def __init__(self, fd: int) -> None:
        if fd < 0:
            raise ValueError("inhibitor fd must be nonnegative")
        self._fd: int | None = fd

    @property
    def active(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        """Release the inhibitor exactly once; process exit also closes the FD."""
        fd = self._fd
        if fd is None:
            return
        # Linux releases the descriptor early in close(), even when a later
        # step reports an error. Clear ownership before calling close and never
        # retry: the descriptor number may already have been reused by another
        # thread. EBADF also means this lease no longer owns a live descriptor.
        self._fd = None
        try:
            os.close(fd)
        except OSError as exc:
            if exc.errno in (errno.EBADF, errno.EINTR):
                return
            raise CaptureIdleInhibitorError(
                f"could not release the capture idle inhibitor: {exc}"
            ) from exc


class Login1IdleInhibitorAdapter:
    """Acquire a bounded ``idle:sleep`` block inhibitor only during Start.

    Construction is inert. Tests may inject a private-connection factory and
    Gio/GLib-compatible fakes; production uses a short-lived private system-bus
    connection and retains only the returned inhibitor FD.
    """

    _BUS_NAME = "org.freedesktop.login1"
    _OBJECT_PATH = "/org/freedesktop/login1"
    _INTERFACE = "org.freedesktop.login1.Manager"
    _WHAT = "idle:sleep"
    _WHO = "Hearth"
    _WHY = "Capture session is active"
    _MODE = "block"

    def __init__(
        self,
        *,
        timeout_ms: int = 3000,
        connection_factory: Callable[[], Any] | None = None,
        gio: Any = Gio,
        glib: Any = GLib,
    ) -> None:
        if not 100 <= timeout_ms <= 10000:
            raise ValueError("login1 inhibitor timeout must be between 100 and 10000 ms")
        self._timeout_ms = timeout_ms
        self._gio = gio
        self._glib = glib
        self._connection_factory = connection_factory or self._open_private_system_bus

    def _open_private_system_bus(self) -> Any:
        address = self._gio.dbus_address_get_for_bus_sync(self._gio.BusType.SYSTEM, None)
        flags = (
            self._gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | self._gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        )
        return self._gio.DBusConnection.new_for_address_sync(address, flags, None, None)

    @staticmethod
    def _close_connection(connection: Any) -> None:
        if connection is not None and connection.close_sync(None) is False:
            raise RuntimeError("private system-bus connection did not close")

    def acquire(self) -> Login1IdleInhibitorLease:
        """Obtain the logind FD; any failure aborts Start rather than running unprotected."""
        connection = None
        fd: int | None = None
        try:
            connection = self._connection_factory()
            parameters = self._glib.Variant(
                "(ssss)", (self._WHAT, self._WHO, self._WHY, self._MODE)
            )
            reply_type = self._glib.VariantType.new("(h)")
            reply, fd_list = connection.call_with_unix_fd_list_sync(
                self._BUS_NAME,
                self._OBJECT_PATH,
                self._INTERFACE,
                "Inhibit",
                parameters,
                reply_type,
                self._gio.DBusCallFlags.NONE,
                self._timeout_ms,
                None,
                None,
            )
            unpacked = reply.unpack()
            if not isinstance(unpacked, tuple) or len(unpacked) != 1:
                raise RuntimeError("login1 returned an invalid inhibitor handle")
            handle = unpacked[0]
            if not isinstance(handle, int) or handle < 0 or fd_list is None:
                raise RuntimeError("login1 did not return an inhibitor file descriptor")
            fd = fd_list.get(handle)
            if not isinstance(fd, int) or fd < 0:
                raise RuntimeError("login1 returned an invalid inhibitor file descriptor")
            os.fstat(fd)
            os.set_inheritable(fd, False)
            self._close_connection(connection)
            connection = None
            lease = Login1IdleInhibitorLease(fd)
            fd = None  # Ownership has moved to the lease.
            return lease
        except Exception as exc:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if connection is not None:
                try:
                    self._close_connection(connection)
                except Exception:
                    pass
            if isinstance(exc, CaptureIdleInhibitorError):
                raise
            raise CaptureIdleInhibitorError(
                f"could not acquire the system idle/sleep inhibitor: {exc}"
            ) from exc
