"""Wire format for the single-instance control socket.

One newline-terminated UTF-8 message per direction. The old implementation
used a fixed ``recv(256)``, so any reply longer than that -- the ``get_sinks``
JSON is about 300 bytes -- came back truncated into invalid JSON. Framing is
defined here, once, and tested against an oversized payload.
"""

from __future__ import annotations

import socket

from hearth.errors import IpcError

#: Ceiling on a single message. A peer that never sends a newline cannot grow
#: our memory without bound; it hits this and the read ends.
MAX_MSG = 1 << 20

#: Default per-connection socket timeout, seconds.
TIMEOUT = 2.0

#: Commands the server understands. Anything else gets a clear error back
#: instead of being silently treated as a no-op.
COMMANDS: frozenset[str] = frozenset({"show", "quit", "unmute", "dump", "get_sinks"})

#: Reply sent when a handler returns nothing.
OK = "ok"


def encode(message: str) -> bytes:
    """Frame *message* for the wire.

    An embedded newline would be read as the end of the message, so it is
    rejected rather than silently splitting the payload in two.
    """
    if "\n" in message:
        raise IpcError("IPC messages may not contain a newline")
    data = (message + "\n").encode()
    if len(data) > MAX_MSG:
        raise IpcError(f"IPC message too large: {len(data)} bytes")
    return data


def read_line(sock: socket.socket, limit: int = MAX_MSG) -> str:
    """Read one bounded newline-terminated message.

    Chunks are reassembled as they arrive, but the read never consumes more
    than the limit. Hitting it without seeing a newline is a protocol error,
    rather than a silently truncated command or reply.
    """
    if limit < 1:
        raise ValueError("limit must be positive")

    chunks: list[bytes] = []
    total = 0
    terminated = False
    while total < limit:
        buf = sock.recv(min(4096, limit - total))
        if not buf:
            break
        newline = buf.find(b"\n")
        if newline != -1:
            chunks.append(buf[:newline])
            terminated = True
            break
        chunks.append(buf)
        total += len(buf)
    else:
        raise IpcError(f"IPC message exceeds {limit} bytes without a newline")

    if not terminated:
        raise IpcError("IPC message ended before its newline terminator")
    return b"".join(chunks).decode(errors="replace").strip()


def is_known(command: str) -> bool:
    return command.split(maxsplit=1)[0] in COMMANDS if command else False
