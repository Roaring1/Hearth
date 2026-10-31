"""Cross-platform advisory locking for Hearth's persistent lock files.

POSIX uses flock on the open file descriptor. Windows uses the MSVCRT
byte-range lock API. It locks byte zero directly; Windows permits locking a
range beyond EOF, so no racy initialization write is needed.
"""

from __future__ import annotations

import errno
import os
import time
from typing import Any

if os.name == "nt":
    import msvcrt

    def lock_file(fd: int) -> None:
        """Acquire an exclusive, blocking lock on byte zero of *fd*."""
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in {
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EDEADLK,
                    getattr(errno, "EDEADLOCK", errno.EDEADLK),
                }:
                    raise
                time.sleep(0.05)

    def unlock_file(fd: int) -> None:
        """Release the exclusive lock held on byte zero of *fd*."""
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    def try_lock_file(fd: int) -> bool:
        """Try to acquire a non-blocking exclusive lock on byte zero."""
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in {
                errno.EACCES,
                errno.EAGAIN,
                errno.EDEADLK,
                getattr(errno, "EDEADLOCK", errno.EDEADLK),
            }:
                return False
            raise

else:
    import fcntl

    _fcntl: Any = fcntl

    def lock_file(fd: int) -> None:
        """Acquire an exclusive, blocking flock."""
        _fcntl.flock(fd, _fcntl.LOCK_EX)

    def unlock_file(fd: int) -> None:
        """Release an exclusive flock."""
        _fcntl.flock(fd, _fcntl.LOCK_UN)

    def try_lock_file(fd: int) -> bool:
        """Try to acquire a non-blocking exclusive flock."""
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
