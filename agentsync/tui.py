"""AgentSync TUI — the AnyDesk-style consent + session control surface.

This is the human-in-the-loop console. It connects to the local daemon over its
Unix-domain socket as a ``role: "control"`` client (see ``docs/PROTOCOL.md``)
and:

* shows **this node's id + label** prominently in the header (AnyDesk shows
  "your ID" front and centre — so do we);
* lists local sessions and remote peers in a **peers panel**, refreshed from
  ``peers`` events;
* pops a **consent prompt** when an ``incoming_connect`` event arrives, letting
  the operator Accept / Reject / Always-allow the requesting node;
* streams a **timestamped transcript** of every daemon event;
* exposes **controls** (pause / resume / stop the selected peer, initiate an
  outbound connect, quit) via footer keybindings;
* survives the daemon being offline — it shows a "daemon offline — retrying…"
  state and reconnects automatically, re-sending ``hello`` each time.

Networking runs inside Textual's own asyncio loop: a single background worker
owns the socket reader/writer, JSON-decodes newline-delimited frames, and posts
them back to the UI via Textual messages so the UI thread never blocks.

Public interface (the CLI imports these — keep the names stable):

* :class:`AgentSyncApp` — the Textual application.
* :func:`run_tui` — build and run the app, defaulting the socket to
  :data:`agentsync.config.SOCKET_PATH`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

from . import config, protocol

log = logging.getLogger("agentsync.tui")

# How long (seconds) to wait before retrying the daemon socket after a failure.
RECONNECT_DELAY = 2.0


# --------------------------------------------------------------------------- #
# Worker -> App messages
# --------------------------------------------------------------------------- #
class DaemonEvent(Message):
    """A decoded ``{"event": ...}`` object pushed up from the socket worker."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload


class DaemonStatus(Message):
    """Connection-state change from the socket worker.

    ``connected`` is True once the socket is open, False while offline/retrying.
    """

    def __init__(self, connected: bool, detail: str = "") -> None:
        super().__init__()
        self.connected = connected
        self.detail = detail


# --------------------------------------------------------------------------- #
# Modal screens
# --------------------------------------------------------------------------- #
class ConsentScreen(ModalScreen[str]):
    """AnyDesk-style consent prompt for an inbound connection request.

    Dismisses with one of ``"accept"``, ``"reject"`` or ``"always"``.
    """

    BINDINGS = [
        Binding("a", "choose('accept')", "Accept", show=True),
        Binding("r,escape", "choose('reject')", "Reject", show=True),
        Binding("w", "choose('always')", "Always allow", show=True),
    ]

    def __init__(self, request_id: str, from_node: str, from_label: str) -> None:
        super().__init__()
        self._request_id = request_id
        self._from_node = from_node
        self._from_label = from_label

    def compose(self) -> ComposeResult:
        with Vertical(id="consent-box"):
            yield Label("Incoming connection request", id="consent-title")
            yield Static(
                f"[b]{self._from_label}[/b] ([dim]{self._from_node}[/dim])\n"
                "wants to connect to this node.",
                id="consent-body",
            )
            with Horizontal(id="consent-buttons"):
                yield Button("Accept (a)", id="accept", variant="success")
                yield Button("Reject (r)", id="reject", variant="error")
                yield Button("Always allow (w)", id="always", variant="primary")

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "reject")

    def action_choose(self, choice: str) -> None:
        self.dismiss(choice)


class PromptScreen(ModalScreen[str | None]):
    """A single-line text prompt. Dismisses with the entered text, or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            yield Label(self._title, id="prompt-title")
            yield Input(placeholder=self._placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- #
# The application
# --------------------------------------------------------------------------- #
class AgentSyncApp(App[None]):
    """The AgentSync control TUI."""

    TITLE = "AgentSync"
    SUB_TITLE = "control surface"

    CSS = """
    #idbar {
        height: 3;
        padding: 0 1;
        background: $boost;
        color: $text;
        content-align: left middle;
    }
    #idbar.offline {
        background: $error 30%;
    }
    #main {
        height: 1fr;
    }
    #peers {
        width: 38%;
        border: round $primary;
    }
    #peers > .datatable--header {
        text-style: bold;
    }
    #transcript {
        width: 1fr;
        border: round $primary;
    }
    #consent-box, #prompt-box {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    #consent-title, #prompt-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #consent-body {
        margin-bottom: 1;
    }
    #consent-buttons {
        height: auto;
        align: center middle;
    }
    #consent-buttons Button {
        margin: 0 1;
    }
    ConsentScreen, PromptScreen {
        align: center middle;
    }
    """

    BINDINGS = [
        Binding("p", "control_peer('pause')", "Pause", show=True),
        Binding("u", "control_peer('resume')", "Resume", show=True),
        Binding("s", "control_peer('stop')", "Stop", show=True),
        Binding("c", "connect_peer", "Connect", show=True),
        Binding("r", "refresh_peers", "Refresh", show=True),
        Binding("q,ctrl+c", "quit", "Quit", show=True),
    ]

    def __init__(self, socket_path: str | None = None) -> None:
        super().__init__()
        self._socket_path: str = socket_path or str(config.SOCKET_PATH)
        # Identity reported by the daemon's `welcome` event.
        self._node_id: str = "?"
        self._label: str = "?"
        self._session_id: str = "?"
        self._connected: bool = False
        # The most recent peers payload, used to map table rows -> targets.
        self._row_targets: list[str] = []
        # Nodes the operator chose to "always allow" (in-memory only).
        self._always_allow: set[str] = set()
        # Outbound command queue drained by the socket worker.
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._writer: asyncio.StreamWriter | None = None

    # -- layout ------------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._idbar_text(), id="idbar")
        with Horizontal(id="main"):
            table: DataTable = DataTable(id="peers", cursor_type="row", zebra_stripes=True)
            yield table
            yield RichLog(id="transcript", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#peers", DataTable)
        table.add_columns("Kind", "Target", "Label", "State")
        self._log_line("[dim]Starting AgentSync control surface…[/dim]")
        self._socket_worker()

    # -- header / id bar ---------------------------------------------------- #
    def _idbar_text(self) -> str:
        state = "[green]online[/green]" if self._connected else "[red]offline — retrying…[/red]"
        return (
            f"Your ID: [b]{self._node_id}[/b]   "
            f"Label: [b]{self._label}[/b]   "
            f"Session: [dim]{self._session_id}[/dim]   "
            f"Daemon: {state}"
        )

    def _refresh_idbar(self) -> None:
        bar = self.query_one("#idbar", Static)
        bar.update(self._idbar_text())
        bar.set_class(not self._connected, "offline")

    # -- transcript --------------------------------------------------------- #
    def _log_line(self, markup: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.query_one("#transcript", RichLog).write(f"[dim]{stamp}[/dim] {markup}")

    # -- socket worker ------------------------------------------------------ #
    @work(name="socket", group="net", exclusive=True)
    async def _socket_worker(self) -> None:
        """Own the daemon connection: connect, hello, read+write, reconnect.

        Runs on Textual's event loop. Inbound frames are posted to the app as
        :class:`DaemonEvent`; connection-state changes as :class:`DaemonStatus`.
        Outbound commands are pulled from :attr:`_outbox`.
        """
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(self._socket_path, limit=16 * 1024 * 1024)
            except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                self.post_message(DaemonStatus(False, str(exc)))
                await asyncio.sleep(RECONNECT_DELAY)
                continue

            self._writer = writer
            self.post_message(DaemonStatus(True))
            # First frame must be a hello; we register as a control client.
            await self._raw_send(writer, {"cmd": "hello", "label": "tui", "role": "control"})
            await self._raw_send(writer, {"cmd": "peers"})

            sender = asyncio.create_task(self._drain_outbox(writer))
            try:
                while True:
                    line = await reader.readline()
                    if not line:  # daemon closed the socket
                        break
                    try:
                        payload = json.loads(line.decode())
                    except json.JSONDecodeError:
                        log.warning("dropping non-JSON frame from daemon")
                        continue
                    self.post_message(DaemonEvent(payload))
            except (ConnectionResetError, asyncio.IncompleteReadError, OSError) as exc:
                log.info("daemon connection dropped: %s", exc)
            finally:
                sender.cancel()
                self._writer = None
                try:
                    writer.close()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    pass
                self.post_message(DaemonStatus(False, "connection dropped"))
            await asyncio.sleep(RECONNECT_DELAY)

    async def _drain_outbox(self, writer: asyncio.StreamWriter) -> None:
        """Forward queued outbound commands to the daemon while connected."""
        while True:
            obj = await self._outbox.get()
            try:
                await self._raw_send(writer, obj)
            except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                log.info("failed to send to daemon: %s", exc)
                # Re-queue so it is retried after reconnect, then stop draining.
                self._outbox.put_nowait(obj)
                return

    @staticmethod
    async def _raw_send(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
        writer.write(json.dumps(obj).encode() + b"\n")
        await writer.drain()

    def _send(self, obj: dict[str, Any]) -> None:
        """Queue a command for the daemon (non-blocking, from the UI thread)."""
        self._outbox.put_nowait(obj)

    # -- message handlers --------------------------------------------------- #
    @on(DaemonStatus)
    def _on_status(self, message: DaemonStatus) -> None:
        was = self._connected
        self._connected = message.connected
        self._refresh_idbar()
        if message.connected and not was:
            self._log_line("[green]Connected to daemon[/green]")
        elif not message.connected and was:
            self._log_line(
                f"[red]Daemon offline[/red] [dim]({message.detail or 'retrying'})[/dim]"
            )
        elif not message.connected:
            # Repeated offline notices (socket missing) — log once-ish, quietly.
            log.debug("daemon still offline: %s", message.detail)

    @on(DaemonEvent)
    def _on_event(self, message: DaemonEvent) -> None:
        payload = message.payload
        event = payload.get("event")
        handler = getattr(self, f"_event_{event}", None)
        if handler is not None:
            handler(payload)
        else:
            self._log_line(f"[dim]event[/dim] {event}: {json.dumps(payload)}")

    # -- per-event handlers ------------------------------------------------- #
    def _event_welcome(self, payload: dict[str, Any]) -> None:
        self._node_id = str(payload.get("node_id", "?"))
        self._label = str(payload.get("label", "?"))
        self._session_id = str(payload.get("session_id", "?"))
        self._refresh_idbar()
        self._log_line(
            f"[b]welcome[/b] — node [b]{self._node_id}[/b] "
            f"label [b]{self._label}[/b] (session {self._session_id})"
        )

    def _event_peers(self, payload: dict[str, Any]) -> None:
        if payload.get("node_id"):
            self._node_id = str(payload["node_id"])
            self._refresh_idbar()
        table = self.query_one("#peers", DataTable)
        table.clear()
        self._row_targets = []
        for sess in payload.get("local", []):
            sid = str(sess.get("session_id", "?"))
            table.add_row("local", sid, str(sess.get("label", "")), "session")
            self._row_targets.append(sid)
        for peer in payload.get("remote", []):
            nid = str(peer.get("node_id", "?"))
            state = "paused" if peer.get("paused") else "active"
            table.add_row("remote", nid, str(peer.get("label", "")), state)
            self._row_targets.append(nid)

    def _event_incoming_connect(self, payload: dict[str, Any]) -> None:
        rid = str(payload.get("request_id"))
        from_node = str(payload.get("from_node", "?"))
        from_label = str(payload.get("from_label", ""))
        if from_node in self._always_allow:
            self._send({"cmd": "accept", "request_id": rid})
            self._log_line(
                f"[green]auto-accepted[/green] {from_label} "
                f"([dim]{from_node}[/dim]) [dim](always allow)[/dim]"
            )
            return
        self._log_line(
            f"[yellow]incoming connect[/yellow] from {from_label} ([dim]{from_node}[/dim])"
        )
        self.push_screen(
            ConsentScreen(rid, from_node, from_label),
            lambda choice: self._resolve_consent(rid, from_node, from_label, choice),
        )

    def _resolve_consent(
        self, rid: str, from_node: str, from_label: str, choice: str | None
    ) -> None:
        if choice == "accept":
            self._send({"cmd": "accept", "request_id": rid})
            self._log_line(f"[green]accepted[/green] {from_label} ([dim]{from_node}[/dim])")
        elif choice == "always":
            self._always_allow.add(from_node)
            # remember=True persists this peer to the daemon's trusted_nodes, so
            # future connections from it auto-accept across sessions and restarts.
            self._send({"cmd": "accept", "request_id": rid, "remember": True})
            self._log_line(
                f"[green]accepted + always allow (saved)[/green] {from_label} "
                f"([dim]{from_node}[/dim])"
            )
        else:  # "reject" or None (dismissed)
            self._send({"cmd": "reject", "request_id": rid})
            self._log_line(f"[red]rejected[/red] {from_label} ([dim]{from_node}[/dim])")

    def _event_connecting(self, payload: dict[str, Any]) -> None:
        self._log_line(
            f"[cyan]connecting[/cyan] to [b]{payload.get('peer')}[/b] "
            f"[dim](request {payload.get('request_id')})[/dim]"
        )

    def _event_connect_result(self, payload: dict[str, Any]) -> None:
        if payload.get("ok"):
            self._log_line(f"[green]connect ok[/green] — {payload.get('peer')}")
        else:
            self._log_line(
                f"[red]connect failed[/red] — {payload.get('peer')}: "
                f"{payload.get('reason', '')}"
            )

    def _event_connected(self, payload: dict[str, Any]) -> None:
        self._log_line(
            f"[green]connected[/green] [b]{payload.get('peer')}[/b] "
            f"([dim]{payload.get('label', '')}[/dim])"
        )

    def _event_ask(self, payload: dict[str, Any]) -> None:
        self._log_line(
            f"[magenta]ask[/magenta] from {payload.get('from_label')} "
            f"([dim]{payload.get('from')}[/dim]): {payload.get('prompt', '')}"
        )

    def _event_reply(self, payload: dict[str, Any]) -> None:
        ok = payload.get("ok", True)
        tag = "[green]reply[/green]" if ok else "[red]reply (err)[/red]"
        self._log_line(
            f"{tag} [dim](request {payload.get('request_id')})[/dim]: "
            f"{payload.get('body', '')}"
        )

    def _event_message(self, payload: dict[str, Any]) -> None:
        self._log_line(
            f"[blue]message[/blue] from {payload.get('from_label')} "
            f"([dim]{payload.get('from')}[/dim]): {payload.get('body', '')}"
        )

    def _event_control(self, payload: dict[str, Any]) -> None:
        self._log_line(
            f"[yellow]control[/yellow] from [dim]{payload.get('from')}[/dim]: "
            f"{payload.get('action')}"
        )

    def _event_peer_gone(self, payload: dict[str, Any]) -> None:
        self._log_line(f"[red]peer gone[/red] — {payload.get('peer')}")

    def _event_error(self, payload: dict[str, Any]) -> None:
        self._log_line(f"[red]error[/red]: {payload.get('message', '')}")

    # -- selection helper --------------------------------------------------- #
    def _selected_target(self) -> str | None:
        table = self.query_one("#peers", DataTable)
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._row_targets)):
            return None
        return self._row_targets[row]

    # -- actions (footer keybindings) --------------------------------------- #
    def action_control_peer(self, action: str) -> None:
        """Send a pause/resume/stop control to the selected peer."""
        if action not in (protocol.PAUSE, protocol.RESUME, protocol.STOP):
            return
        target = self._selected_target()
        if target is None:
            self.notify("Select a peer first.", severity="warning")
            return
        self._send({"cmd": "control", "target": target, "action": action})
        self._log_line(f"[yellow]→ control[/yellow] {action} [b]{target}[/b]")

    def action_connect_peer(self) -> None:
        """Prompt for a peer id and initiate an outbound connect."""
        self.push_screen(
            PromptScreen("Connect to peer (node id or local session id):", "AS-XXXX-XXXX"),
            self._do_connect,
        )

    def _do_connect(self, target: str | None) -> None:
        if not target:
            return
        self._send({"cmd": "connect", "target": target})
        self._log_line(f"[cyan]→ connect[/cyan] [b]{target}[/b]")

    def action_refresh_peers(self) -> None:
        self._send({"cmd": "peers"})


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_tui(socket_path: str | None = None) -> None:
    """Build and run the AgentSync control TUI.

    Args:
        socket_path: Path to the daemon's Unix socket. Defaults to
            :data:`agentsync.config.SOCKET_PATH` when ``None``.
    """
    AgentSyncApp(socket_path=socket_path).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_tui()
