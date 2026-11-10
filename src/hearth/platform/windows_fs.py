"""Windows filesystem security helpers for Hearth.

Hearth keeps configuration, diagnostics, logs, and IPC state under one
per-user root. Windows mode bits are not an adequate equivalent of POSIX
0700/0600, so the root receives a protected DACL granting access only to the
current user, SYSTEM, and local Administrators.
"""

from __future__ import annotations

import csv
import ctypes
import io
import os
import stat
import subprocess
import threading
from pathlib import Path

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_SECURITY_LOCK = threading.RLock()
_SECURED_ROOT: Path | None = None
_SECURED_SID: str | None = None


def _system_tool(name: str) -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    tool = windir / "System32" / name
    if not tool.is_file():
        raise FileNotFoundError(f"Windows system utility not found: {tool}")
    return tool


def current_user_sid() -> str:
    """Return the current process user's SID, independent of localized names."""
    global _SECURED_SID
    if _SECURED_SID is not None:
        return _SECURED_SID

    result = subprocess.run(
        [str(_system_tool("whoami.exe")), "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode != 0:
        raise PermissionError(
            f"could not identify the current Windows user: {result.stderr or result.stdout}"
        )
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if not rows or len(rows[0]) < 2:
        raise PermissionError("whoami did not return a usable Windows user SID")
    sid = rows[0][1].strip()
    if not sid.startswith("S-1-"):
        raise PermissionError("whoami returned an invalid Windows user SID")
    _SECURED_SID = sid
    return sid


def is_reparse_point(path: Path) -> bool:
    """Detect symlinks, junctions, and other Windows reparse points."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32

    attributes = get_attributes(str(path))
    if attributes == _INVALID_FILE_ATTRIBUTES:
        error = ctypes.get_last_error()
        if error in (2, 3):  # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND
            return False
        raise OSError(error, ctypes.FormatError(error), str(path))
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _reset_acl(path: Path, *, recursive: bool) -> None:
    """Remove stale explicit and inherited ACEs before applying a private DACL."""
    command = [str(_system_tool("icacls.exe")), str(path), "/reset"]
    if recursive:
        command.append("/T")

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PermissionError(f"could not reset Hearth ACLs for {path}: {detail}")


def _validate_private_tree(root: Path) -> None:
    """Reject links and special files before recursively changing a private tree."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if is_reparse_point(child):
                        raise PermissionError(
                            f"refusing a reparse point in Hearth's private tree: {child}"
                        )
                    metadata = child.lstat()
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(child)
                    elif not stat.S_ISREG(metadata.st_mode):
                        raise OSError(
                            f"refusing a non-regular file in Hearth's private tree: {child}"
                        )
                    elif metadata.st_nlink != 1:
                        raise OSError(
                            f"refusing a hard-linked file in Hearth's private tree: {child}"
                        )
        except OSError:
            raise


def _apply_private_acl(path: Path, *, recursive: bool) -> None:
    """Protect a Hearth-owned directory using SID-based, non-inherited ACEs."""
    if recursive:
        _validate_private_tree(path)
    _reset_acl(path, recursive=recursive)
    sid = current_user_sid()
    command = [
        str(_system_tool("icacls.exe")),
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"*{sid}:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",  # LocalSystem
        "*S-1-5-32-544:(OI)(CI)F",  # Builtin Administrators
    ]
    # The recursive /reset above restores inheritance on descendants. Apply
    # inheritable ACEs only to the root: combining OI/CI with /T disables
    # inheritance on files while marking their ACEs inherit-only, leaving
    # them without effective access.
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PermissionError(f"could not secure Hearth directory {path}: {detail}")


def ensure_private_root(root: Path) -> Path:
    """Create and protect Hearth's root directory; repair its existing tree."""
    global _SECURED_ROOT
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if is_reparse_point(root):
        raise PermissionError(f"refusing a reparse-point Hearth root: {root}")
    resolved = root.resolve(strict=True)

    with _SECURITY_LOCK:
        if resolved == _SECURED_ROOT:
            return resolved
        _apply_private_acl(resolved, recursive=True)
        _SECURED_ROOT = resolved
    return resolved


def ensure_private_dir(path: Path, *, root: Path | None = None) -> Path:
    """Create a private directory, inheriting Hearth's protected root ACL.

    A path inside *root* inherits its protected DACL. An explicit path outside
    the application root (useful for tests and caller-supplied destinations)
    receives its own protected DACL.
    """
    path = Path(path).absolute()

    if root is None:
        path.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(path):
            raise PermissionError(f"refusing a reparse-point private directory: {path}")
        resolved = path.resolve(strict=True)
        # A caller-supplied directory is not an application-owned tree. Secure
        # the directory itself and let new children inherit its protected DACL.
        _apply_private_acl(resolved, recursive=False)
        return resolved

    root_resolved = ensure_private_root(Path(root))
    if path == root_resolved:
        return root_resolved
    try:
        path.relative_to(root_resolved)
    except ValueError:
        # Explicit test/caller directories outside the application root are
        # protected at the directory itself. New children inherit the DACL;
        # avoid a recursive walk of caller-owned trees.
        path.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(path):
            raise PermissionError(f"refusing a reparse-point private directory: {path}") from None
        resolved = path.resolve(strict=True)
        _apply_private_acl(resolved, recursive=False)
        return resolved

    # Check each existing component before and after creation. This rejects
    # junctions as well as ordinary symlinks and avoids following them.
    candidate = path
    while candidate != root_resolved:
        if candidate == candidate.parent:
            raise PermissionError(f"path escaped Hearth's private root: {path}")
        if candidate.exists() and is_reparse_point(candidate):
            raise PermissionError(f"refusing a reparse point in Hearth's private tree: {candidate}")
        candidate = candidate.parent

    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PermissionError(f"path is outside Hearth's private root: {resolved}") from exc
    return resolved


def ensure_private_file(path: Path, *, root: Path | None = None) -> Path:
    """Validate and protect a regular, single-link file.

    Normal Hearth files inherit the protected app root. Explicit test/caller
    files outside that root receive an independent protected file DACL.
    """
    path = Path(path).absolute()
    root_resolved = ensure_private_root(Path(root)) if root is not None else None
    if root_resolved is None:
        if is_reparse_point(path):
            raise PermissionError(f"refusing a reparse-point private file: {path}")
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"refusing a non-regular private file: {path}")
        if metadata.st_nlink != 1:
            raise OSError(f"refusing a hard-linked private file: {path}")
        resolved = path.resolve(strict=True)
        _apply_private_file_acl(resolved)
        return resolved

    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        if is_reparse_point(path):
            raise PermissionError(f"refusing a reparse-point private file: {path}") from exc
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"refusing a non-regular private file: {path}") from exc
        if metadata.st_nlink != 1:
            raise OSError(f"refusing a hard-linked private file: {path}") from exc
        resolved = path.resolve(strict=True)
        _apply_private_file_acl(resolved)
        return resolved

    candidate = path
    while candidate != root_resolved:
        if candidate == candidate.parent:
            raise PermissionError(f"file escaped Hearth's private root: {path}")
        if candidate.exists() and is_reparse_point(candidate):
            raise PermissionError(f"refusing a reparse point in Hearth's private tree: {candidate}")
        candidate = candidate.parent

    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"refusing a non-regular private file: {path}")
    if metadata.st_nlink != 1:
        raise OSError(f"refusing a hard-linked private file: {path}")
    resolved = path.resolve(strict=True)
    if root_resolved is not None:
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise PermissionError(f"file is outside Hearth's private root: {resolved}") from exc
    _apply_private_file_acl(resolved)
    return resolved


def _apply_private_file_acl(path: Path) -> None:
    """Protect a single Hearth file with a non-inheriting DACL."""
    _reset_acl(path, recursive=False)
    sid = current_user_sid()
    command = [
        str(_system_tool("icacls.exe")),
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"*{sid}:F",
        "*S-1-5-18:F",
        "*S-1-5-32-544:F",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PermissionError(f"could not secure Hearth file {path}: {detail}")


def validate_private_fd(fd: int, path: Path, *, root: Path) -> None:
    """Validate the opened Windows file handle, not just its pathname.

    GetFileInformationByHandle exposes the actual handle's reparse attribute
    and link count. This closes the ordinary path-stat/open gap for lock files.
    """
    import msvcrt
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", FILETIME),
            ("ftLastAccessTime", FILETIME),
            ("ftLastWriteTime", FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    handle = msvcrt.get_osfhandle(fd)
    info = BY_HANDLE_FILE_INFORMATION()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    get_info.restype = wintypes.BOOL
    if not get_info(wintypes.HANDLE(handle), ctypes.byref(info)):
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error), str(path))

    if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise PermissionError(f"refusing a reparse-point open handle: {path}")
    if info.nNumberOfLinks != 1:
        raise OSError(f"refusing a hard-linked open file: {path}")

    ensure_private_file(path, root=root)
