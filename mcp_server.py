"""mcp_server.py — an in-app MCP server that lets Claude drive the live viewer.

The running desktop app hosts this server over streamable-HTTP in a daemon thread.
Read-only query tools (list/find nets, net/part pins) read the immutable PcbModel
directly. Mutating tools (show/clear probe points) emit a Qt signal on a bridge
QObject that lives on the GUI thread, so widget updates are marshalled onto the Qt
event loop and never touched from the server thread.

Connect from Claude Code:
    claude mcp add --transport http altium-probe http://127.0.0.1:4323/mcp
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

_INSTRUCTIONS = (
    "Drive the Altium Assembly Tool's live Board Reader to help locate PCB probe "
    "points during bring-up. Use find_nets/list_nets and net_pins/part_pins to "
    "discover nets and pads, then show_probe_points(nets=[...], pins=['IC6-8', ...]) "
    "to spotlight them on screen (labels + outlines, zoomed to fit). "
    "clear_probe_points() removes the spotlight. Pins are 'REFDES-PAD' strings."
)


class ProbeBridge(QObject):
    """Carries MCP requests from the server thread to the GUI thread.

    Emitting a signal from another thread with the default (auto) connection queues
    the call onto the receiver's thread — here, the Qt main thread.
    """

    show_requested = Signal(list, list)   # (nets, pins)
    clear_requested = Signal()


def _pin_dict(p) -> dict:
    return {
        "pin": p.pin_name,
        "refdes": p.refdes,
        "pad": p.pad,
        "net": p.net,
        "side": p.side,
    }


def build_server(window):
    """Create the MCP server and the GUI bridge wired to the given MainWindow.

    Returns (server, bridge). The bridge is stashed on the window so it is not
    garbage-collected while the server runs.
    """
    from mcp.server.mcpserver import MCPServer

    bridge = ProbeBridge()
    bridge.show_requested.connect(window.show_probe_points)
    bridge.clear_requested.connect(window.clear_probe_points)
    window._probe_bridge = bridge  # keep a strong reference alive

    server = MCPServer("altium-probe", instructions=_INSTRUCTIONS)

    @server.tool()
    def board_status() -> dict:
        """Whether a board is loaded, its name, and the current view mode/side."""
        m = window.model()
        if m is None:
            return {"loaded": False}
        return {
            "loaded": True,
            "board_name": m.board_name,
            "mode": window._mode,
            "view_side": window._view_side,
        }

    @server.tool()
    def list_nets() -> list[str]:
        """Every net name on the board (naturally sorted)."""
        m = window.model()
        return list(m.net_names) if m else []

    @server.tool()
    def find_nets(query: str) -> list[str]:
        """Net names containing the query (case-insensitive, exact matches first)."""
        m = window.model()
        return m.find_nets(query) if m else []

    @server.tool()
    def net_pins(net: str) -> list[dict]:
        """Probe points on a net: each pad as {pin, refdes, pad, net, side}."""
        m = window.model()
        return [_pin_dict(p) for p in m.net_pins(net)] if m else []

    @server.tool()
    def part_pins(refdes: str) -> list[dict]:
        """Connected pads of a component: each as {pin, refdes, pad, net, side}."""
        m = window.model()
        return [_pin_dict(p) for p in m.pins_for_refdes(refdes)] if m else []

    @server.tool()
    def show_probe_points(
        nets: list[str] | None = None, pins: list[str] | None = None
    ) -> dict:
        """Spotlight probe points in the live viewer.

        nets: net names whose pads to show. pins: 'REFDES-PAD' tokens (e.g. 'IC6-8').
        Switches the viewer to Board Reader, labels + outlines only these pads, and
        zooms to fit. Returns the resolved pads and any tokens that did not match.
        """
        nets = nets or []
        pins = pins or []
        m = window.model()
        if m is None:
            return {"ok": False, "error": "no board loaded"}
        resolved, unresolved = window.resolve_probe_pins(nets, pins)
        bridge.show_requested.emit(list(nets), list(pins))
        return {
            "ok": True,
            "count": len(resolved),
            "shown": [_pin_dict(p) for p in resolved],
            "unresolved": unresolved,
        }

    @server.tool()
    def clear_probe_points() -> dict:
        """Remove the probe spotlight and return to the normal reader view."""
        bridge.clear_requested.emit()
        return {"ok": True}

    return server, bridge


def start_mcp_server(window, port: int = 4323, host: str = "127.0.0.1") -> threading.Thread:
    """Start the MCP server in a daemon thread. Call from the GUI thread so the
    bridge's signal connections are created there."""
    server, _bridge = build_server(window)

    def _serve() -> None:
        server.run(transport="streamable-http", host=host, port=port)

    thread = threading.Thread(target=_serve, name="mcp-server", daemon=True)
    thread.start()
    return thread
