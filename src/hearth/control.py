"""What the control socket says, with no toolkit attached.

The socket used to be answered by the GTK3 window, so retiring that window
would have silently taken ``hearth --quit``, the desktop entry's actions and
Padfire's three-second ``get_sinks`` poll with it. The grammar lives here
instead: the GTK4 app supplies callables, this module decides what a request
means and what goes back on the wire, and the whole thing stays testable
without a display.

Replies are plain text. ``get_sinks`` is JSON because Padfire parses it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping

__all__ = ["UNKNOWN", "dispatch", "sinks_payload", "verb"]

#: Reply for a request nobody claims. The old window answered a bare
#: "unknown", and Padfire's probe treats any non-JSON reply as "offline",
#: so the text can improve but the shape cannot.
UNKNOWN = "unknown"


def verb(request: str) -> str:
    """The command word of *request*, ignoring any arguments.

    Padfire sends ``padfire_status <json>``; the argument was never read,
    but the request still has to be recognised by its first word.
    """
    return request.strip().split(maxsplit=1)[0] if request.strip() else ""


def dispatch(request: str, actions: Mapping[str, Callable[[], str]]) -> str:
    """Run the action for *request* and return its reply.

    An action that raises is the caller's problem to log: the server frames
    whatever comes back, and swallowing the error here would make a broken
    command indistinguishable from a working one.
    """
    action = actions.get(verb(request))
    return action() if action is not None else UNKNOWN


def sinks_payload(rows: Iterable[tuple[str, str, int, bool]]) -> str:
    """Padfire's ``get_sinks`` reply, built from state the window already has.

    Each row is ``(sink, label, volume, muted)``. The old implementation ran
    two ``pactl`` calls per bus per poll -- ten subprocesses every three
    seconds for a panel that is usually not even visible. The window
    refreshes this state anyway, so the socket should read it, not re-earn
    it.

    Labels are upper-cased because that is what the old reply sent and what
    Padfire prints.
    """
    return json.dumps(
        {
            sink: {"vol": int(volume), "mute": bool(muted), "label": label.upper()}
            for sink, label, volume, muted in rows
        }
    )
