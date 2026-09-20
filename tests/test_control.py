"""Control-socket grammar.

The socket outlived the window that used to answer it, and Padfire parses
one of the replies, so the shape of these strings is a contract.
"""

from __future__ import annotations

import json

import pytest

from hearth import control


def test_unknown_command_is_refused_not_guessed() -> None:
    assert control.dispatch("reboot", {"show": lambda: "ok"}) == control.UNKNOWN


def test_empty_request_does_not_match_an_action() -> None:
    assert control.dispatch("   ", {"": lambda: "ok", "show": lambda: "ok"}) == "ok"
    assert control.verb("   ") == ""


def test_arguments_are_ignored_but_the_verb_still_matches() -> None:
    # Padfire sends "padfire_status <json>"; the old window answered "ok".
    assert control.dispatch('padfire_status {"p":1}', {"padfire_status": lambda: "ok"}) == "ok"


def test_a_raising_action_is_not_swallowed() -> None:
    def boom() -> str:
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        control.dispatch("quit", {"quit": boom})


def test_sinks_payload_is_the_json_padfire_expects() -> None:
    payload = json.loads(
        control.sinks_payload([("vm_game", "Game", 74, False), ("mic_b1", "Mic B1", 100, True)])
    )
    assert payload == {
        "vm_game": {"vol": 74, "mute": False, "label": "GAME"},
        "mic_b1": {"vol": 100, "mute": True, "label": "MIC B1"},
    }


def test_sinks_payload_without_a_window_is_still_valid_json() -> None:
    assert json.loads(control.sinks_payload(())) == {}
