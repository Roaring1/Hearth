"""Tests for the PipeWire link parser, against a recorded live graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import proc
from hearth.audio import links
from hearth.errors import AudioError

FIXTURE = Path(__file__).parent / "fixtures" / "pw_link_list.txt"


@pytest.fixture
def graph() -> list[links.Link]:
    return links.parse(FIXTURE.read_text())


def test_parses_the_recorded_graph(graph: list[links.Link]) -> None:
    assert graph, "fixture should contain links"
    assert all(link.source and link.target for link in graph)


def test_node_names_may_contain_spaces_and_parentheses() -> None:
    assert links.node_of("Roaring VU (internal):input_FL") == "Roaring VU (internal)"
    assert links.node_of("vm_game:monitor_FL") == "vm_game"
    assert links.node_of("no_colon_here") == "no_colon_here"


def test_share_bus_is_fed_by_game_and_music(graph: list[links.Link]) -> None:
    """The oneshot that wires this exits immediately, so only the graph knows."""
    assert links.channel_count(graph, "vm_game", "vm_share") == 2
    assert links.channel_count(graph, "vm_music", "vm_share") == 2


def test_chat_never_reaches_the_share_bus(graph: list[links.Link]) -> None:
    """Incoming Discord voice in a screen share is the echo bug; guard it."""
    assert not links.is_connected(graph, "vm_chat", "vm_share")


def test_feeders_are_distinct_and_ordered(graph: list[links.Link]) -> None:
    assert links.feeders(graph, "vm_share") == ["vm_game", "vm_music"]


def test_incoming_arrows_are_normalised_to_signal_flow() -> None:
    text = "vm_chat:playback_FL\n  |<- vesktop:output_FL\n"
    (link,) = links.parse(text)
    assert link.source == "vesktop:output_FL"
    assert link.target == "vm_chat:playback_FL"


def test_the_same_link_seen_from_both_ends_is_counted_once() -> None:
    text = (
        "vm_game:monitor_FL\n"
        "  |-> vm_share:playback_FL\n"
        "vm_share:playback_FL\n"
        "  |<- vm_game:monitor_FL\n"
    )
    assert len(links.parse(text)) == 1


def test_junk_lines_are_ignored() -> None:
    text = "\n  |-> orphan:before_any_node\nnode_a:out\n  strange line\n  |-> node_b:in\n"
    (link,) = links.parse(text)
    assert link.source_node == "node_a"
    assert link.target_node == "node_b"


def test_list_links_refuses_to_report_a_false_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed probe must raise, never look like an empty graph."""
    monkeypatch.setattr(links.proc, "have", lambda _name: True)

    def fake_run(argv: list[str], **kwargs: object) -> proc.Result:
        return proc.Result(tuple(argv), 1, "", "remote error: Host is down")

    monkeypatch.setattr(links.proc, "run", fake_run)
    with pytest.raises(AudioError) as excinfo:
        links.list_links()
    assert "Host is down" in str(excinfo.value)


def test_list_links_requires_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links.proc, "have", lambda _name: False)
    with pytest.raises(AudioError):
        links.list_links()
