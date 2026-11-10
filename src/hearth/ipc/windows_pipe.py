"""Authenticated per-user named-pipe IPC for Windows.

The public API mirrors the Unix socket Server/client implementation while
retaining Hearth's newline-delimited UTF-8 message protocol. The random
authentication key is stored under Hearth's protected LocalAppData root.
A byte-range lock is held for the lifetime of the listener so a second
instance cannot create another named-pipe server under the same name.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from pathlib import Path

from hearth import paths
from hearth.errors import IpcError
from hearth.ipc import protocol
from hearth.platform.locking import try_lock_file, unlock_file

log = logging.getLogger(__name__)

Handler = Callable[[str], str | None]
_SHUTDOWN = b"__hearth_internal_shutdown__\n"
_ACCEPT_BACKOFF = 0.2


def _pipe_address() -> str:
    from hearth.platform.windows_fs import current_user_sid

    sid = current_user_sid()
    suffix = hashlib.sha256(sid.encode("ascii")).hexdigest()[:20]
    return rf"\\.\pipe\hearth-{suffix}"


def _key_path() -> Path:
    return paths.runtime_dir() / "ipc-auth.key"


def _authkey(*, create: bool) -> bytes | None:
    path = _key_path()
    paths.ensure_private_dir(path.parent)

    if not path.exists():
        if not create:
            return None
        payload = secrets.token_bytes(32).hex().encode("ascii") + b"\n"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)

    if not path.exists():
        return None
    paths.ensure_private_file(path)
    try:
        value = bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError) as exc:
        raise OSError(f"Hearth IPC authentication key is invalid: {path}") from exc
    if len(value) != 32:
        raise OSError(f"Hearth IPC authentication key has an invalid length: {path}")
    return value


def _decode_frame(data: bytes) -> str:
    if not data or len(data) > protocol.MAX_MSG or not data.endswith(b"\n"):
        raise IpcError("invalid or oversized IPC frame")
    body = data[:-1]
    if b"\n" in body or b"\r" in body:
        raise IpcError("IPC frame contains an embedded line break")
    return body.decode("utf-8", errors="replace").strip()


def send(command: str, *, socket_path: Path | None = None, timeout: float = 0.5) -> str | None:
    """Send a command over the authenticated named pipe, or return None."""
    try:
        data = protocol.encode(command)
        key = _authkey(create=False)
        if key is None:
            return None
        conn = Client(_pipe_address(), family="AF_PIPE", authkey=key)
        try:
            conn.send_bytes(data)
            if not conn.poll(timeout):
                return None
            reply = conn.recv_bytes(protocol.MAX_MSG)
        finally:
            conn.close()
        return _decode_frame(reply)
    except (AuthenticationError, OSError, EOFError, TimeoutError, IpcError) as exc:
        log.debug("Windows IPC send %r failed: %s", command, exc)
        return None


def is_running(*, socket_path: Path | None = None) -> bool:
    return send("show", socket_path=socket_path) is not None


def clear_stale_socket(socket_path: Path | None = None) -> bool:
    """Windows named pipes are kernel objects; stale path unlinking is N/A."""
    return False


class Server:
    """Windows named-pipe server with single-instance ownership."""

    def __init__(self, handler: Handler, *, socket_path: Path | None = None) -> None:
        self._handler = handler
        self.path = socket_path or paths.socket_file()
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._lock_fd: int | None = None
        self._key: bytes | None = None
        self._address = _pipe_address()

    def _acquire_instance_lock(self) -> None:
        lock_path = paths.runtime_dir() / "instance.lock"
        paths.ensure_private_dir(lock_path.parent)
        if lock_path.exists():
            paths.ensure_private_file(lock_path)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            paths.ensure_private_fd(fd, lock_path)
            if not try_lock_file(fd):
                raise OSError(errno.EADDRINUSE, "a Hearth Windows instance already owns IPC")
        except BaseException:
            os.close(fd)
            raise
        self._lock_fd = fd

    def start(self) -> threading.Thread:
        """Acquire exclusivity, bind the pipe, and start the accept thread."""
        if self._thread is not None:
            raise RuntimeError("Hearth IPC server is already started")
        self._stopping.clear()
        paths.ensure_dirs()
        self._acquire_instance_lock()
        try:
            self._key = _authkey(create=True)
            if self._key is None:
                raise OSError("could not create the Hearth IPC authentication key")
            self._listener = Listener(
                self._address,
                family="AF_PIPE",
                authkey=self._key,
                backlog=4,
            )
            thread = threading.Thread(
                target=self.serve_forever,
                daemon=True,
                name="hearth-ipc-pipe",
            )
            thread.start()
            self._thread = thread
            return thread
        except BaseException:
            self._close_listener()
            self._release_instance_lock()
            raise

    def serve_forever(self) -> None:
        listener = self._listener
        if listener is None:
            raise OSError("Windows IPC listener has not been created")
        while not self._stopping.is_set():
            try:
                conn = listener.accept()
            except AuthenticationError as exc:
                log.warning("Windows IPC rejected an unauthenticated client: %s", exc)
                continue
            except (OSError, EOFError) as exc:
                if self._stopping.is_set():
                    break
                log.warning("Windows IPC accept failed: %s", exc)
                time.sleep(_ACCEPT_BACKOFF)
                continue

            try:
                request = _decode_frame(conn.recv_bytes(protocol.MAX_MSG))
                if request == _SHUTDOWN.decode("ascii").strip():
                    continue
                reply = self._handler(request) or protocol.OK
                conn.send_bytes(protocol.encode(str(reply)))
            except Exception as exc:
                log.warning("Windows IPC handler error: %s", exc)
            finally:
                with suppress(OSError):
                    conn.close()

    def _close_listener(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            with suppress(OSError):
                listener.close()

    def _release_instance_lock(self) -> None:
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            try:
                unlock_file(fd)
            except OSError as exc:
                log.debug("could not release Windows IPC instance lock: %s", exc)
            finally:
                os.close(fd)

    def stop(self, *, timeout: float = 2.0) -> None:
        """Wake accept(), join the worker, and release the per-user lock."""
        self._stopping.set()
        key = self._key
        if key is not None:
            try:
                conn = Client(self._address, family="AF_PIPE", authkey=key)
                try:
                    conn.send_bytes(_SHUTDOWN)
                finally:
                    conn.close()
            except (AuthenticationError, OSError, EOFError, TimeoutError):
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._close_listener()
                thread.join(timeout=timeout)
            if thread.is_alive():
                log.error("Windows IPC worker did not stop; retaining its instance lock")
                return
        self._thread = None
        self._close_listener()
        self._release_instance_lock()
