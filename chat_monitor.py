#!/usr/bin/env python3
"""
Meshtastic Chat Monitor
A terminal UI for monitoring messages and sending replies.
Press 's' to send a message, 'q' to quit.
"""
import time
import sys
from datetime import datetime
import meshtastic
import meshtastic.serial_interface
from pubsub import pub
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.live import Live
from rich.table import Table
from rich.text import Text
import threading
import tty
import termios
import select

# Terminal/shared state
stdin_fd = None
original_term_settings = None
request_input_event = threading.Event()
shutdown_event = threading.Event()

# ====== CONFIG ======
SERIAL_PORT = None  # set explicitly if needed, e.g. "/dev/ttyUSB0"
MAX_MESSAGES = 50  # Maximum number of messages to keep in history
# ====================

console = Console()
messages = []
iface = None
my_node_id = None
live_display = None
input_mode = False
current_table = None  # The single table object we'll update


def on_connection(interface, topic=pub.AUTO_TOPIC):
    global my_node_id
    try:
        node = interface.getMyNodeInfo()
        my_node_id = node["user"]["id"]
        log_system(f"Connected: {node['user']['shortName']} ({my_node_id})")
    except Exception as e:
        log_system(f"Connection warning: {e}")


def on_disconnect(interface=None, topic=pub.AUTO_TOPIC):
    log_system("Disconnected from device", error=True)


def on_receive(packet, interface):
    """Monitor all received packets"""
    decoded = packet.get("decoded", {})
    portnum = decoded.get("portnum", "unknown")

    # Skip non-text message types
    ignored_types = [
        "POSITION_APP",
        "TELEMETRY_APP",
        "ROUTING_APP",
        "NODEINFO_APP",
        "ADMIN_APP",
        "unknown",
    ]
    if portnum in ignored_types:
        return

    # Only process text messages
    if portnum == "TEXT_MESSAGE_APP" or portnum == 1:
        message_content = decoded.get("text")
        if not message_content and "payload" in decoded:
            try:
                payload = decoded["payload"]
                if isinstance(payload, bytes):
                    message_content = payload.decode("utf-8")
                elif isinstance(payload, str):
                    message_content = payload
            except Exception:
                return

        if message_content:
            from_id = packet.get("fromId", packet.get("from", "unknown"))
            to_id = packet.get("toId", packet.get("to", "unknown"))
            packet_id = packet.get("id")

            log_message(from_id, to_id, message_content, packet_id)


def log_message(from_id, to_id, content, packet_id=None):
    """Add a message to the log"""
    # Skip empty messages
    if not content or not content.strip():
        return

    timestamp = datetime.now().strftime("%H:%M:%S")
    messages.append(
        {
            "time": timestamp,
            "from": from_id,
            "to": to_id,
            "content": content,
            "packet_id": packet_id,
            "type": "message",
        }
    )

    # Keep only last MAX_MESSAGES
    if len(messages) > MAX_MESSAGES:
        messages.pop(0)

    # Update the live display
    update_display()


def log_system(message, error=False):
    """Add a system message to the log"""
    # Skip empty system messages
    if not message or not message.strip():
        return

    timestamp = datetime.now().strftime("%H:%M:%S")
    messages.append(
        {"time": timestamp, "content": message, "type": "system", "error": error}
    )

    if len(messages) > MAX_MESSAGES:
        messages.pop(0)

    # Update the live display
    update_display()


def rebuild_table():
    """Rebuild the table from current messages"""
    global current_table

    # Create a fresh table
    current_table = Table(
        show_header=True, header_style="bold magenta", show_lines=True, expand=True
    )
    current_table.add_column("Time", style="dim", width=8)
    current_table.add_column("From", style="cyan", width=15)
    current_table.add_column("To", style="cyan", width=15)
    current_table.add_column("Message", style="white")

    # Get last 30 messages with content
    display_messages = messages[-30:] if len(messages) > 30 else messages

    for msg in display_messages:
        if msg["type"] == "system":
            style = "red" if msg.get("error") else "yellow"
            current_table.add_row(
                msg["time"],
                Text("[SYSTEM]", style=style),
                "",
                Text(msg["content"], style=style),
            )
        else:
            # Highlight messages from us
            from_id = msg.get("from", "unknown")
            to_id = msg.get("to", "unknown")
            from_style = "green bold" if from_id == my_node_id else "cyan"
            to_style = "green bold" if to_id == my_node_id else "cyan"

            current_table.add_row(
                msg["time"],
                Text(str(from_id), style=from_style),
                Text(str(to_id), style=to_style),
                msg["content"],
            )


def update_display():
    """Update the live display with current messages"""
    if live_display and not input_mode:
        rebuild_table()
        panel = Panel(
            current_table,
            title="[bold blue]Meshtastic Chat Monitor[/bold blue]",
            subtitle="[dim]Press 's' to send message, 'q' to quit[/dim]",
            border_style="blue",
        )
        live_display.update(panel, refresh=True)


def _read_line_raw(prompt_text: str, default: str | None = None) -> str:
    """Read a line in raw mode with basic editing and ESC/Ctrl+C cancel.

    Returns the entered string. If empty and default is provided, returns default.
    Raises KeyboardInterrupt on ESC or Ctrl+C to signal cancel.
    """
    # Show prompt
    if default is not None and default != "":
        prompt_full = f"{prompt_text} [{default}]: "
    else:
        prompt_full = f"{prompt_text}: "

    sys.stdout.write(prompt_full)
    sys.stdout.flush()

    buf = []
    while True:
        # Use select to avoid blocking forever in edge cases
        r, _, _ = select.select([sys.stdin], [], [], 0.1)
        if not r:
            # allow cooperative multitasking
            if shutdown_event.is_set():
                raise KeyboardInterrupt
            continue
        ch = sys.stdin.read(1)
        if not ch:
            continue
        code = ch

        # Enter / Return
        if code in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            s = "".join(buf)
            if not s and default is not None:
                return default
            return s
        # ESC cancels
        if code == "\x1b":
            # print a newline and raise cancel
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise KeyboardInterrupt
        # Ctrl+C cancels
        if code == "\x03":
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise KeyboardInterrupt
        # Backspace/Delete
        if code in ("\x7f", "\b"):
            if buf:
                buf.pop()
                # erase last char on terminal
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        # Printable characters
        if " " <= code <= "~":
            buf.append(code)
            sys.stdout.write(code)
            sys.stdout.flush()
            continue
        # ignore others


def send_message_prompt():
    """Prompt user to send a message (runs in main thread)."""
    global input_mode

    input_mode = True
    # Pause live updates; keep raw mode and pause input thread via input_mode flag
    if live_display:
        live_display.stop()

    try:
        console.print("\n[bold cyan]Send Message[/bold cyan]")

        # Destination (supports default)
        dest = _read_line_raw("Destination", default="^all")

        # Message
        message = _read_line_raw("Message")

        if message:
            try:
                console.print(f"[yellow]Sending to {dest}...[/yellow]")
                iface.sendText(message, destinationId=dest, wantAck=False)
                log_system(f"Sent message to {dest}")
            except Exception as e:
                log_system(f"Failed to send: {e}", error=True)
        else:
            log_system("Message cancelled")
    except KeyboardInterrupt:
        # Ctrl+C during input: cancel and resume live view
        log_system("Message input cancelled")
    finally:
        input_mode = False
        # Rebuild and resume live display, then return to raw mode for key handling
        if live_display:
            rebuild_table()
            panel = Panel(
                current_table,
                title="[bold blue]Meshtastic Chat Monitor[/bold blue]",
                subtitle="[dim]Press 's' to send message, 'q' to quit[/dim]",
                border_style="blue",
            )
            live_display.start()
            live_display.update(panel, refresh=True)
        # Ensure raw mode is active for background key reader
        if stdin_fd is not None:
            try:
                tty.setraw(stdin_fd)
            except Exception:
                pass


def input_thread():
    """Thread to handle single-key commands ('s' to send, 'q' to quit)."""
    try:
        if stdin_fd is not None:
            tty.setraw(stdin_fd)
        while True:
            if shutdown_event.is_set():
                break
            # Do not consume stdin while in input mode
            if input_mode:
                time.sleep(0.05)
                continue

            # Poll for key press without blocking indefinitely
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not r:
                continue
            ch = sys.stdin.read(1)

            if ch == "q":
                log_system("Shutting down...")
                shutdown_event.set()
                break
            elif ch == "s":
                request_input_event.set()
            # ESC in live mode: no-op, reserved for cancel within prompt

    except Exception as e:
        log_system(f"Input thread error: {e}", error=True)


def main():
    global iface, live_display, current_table

    # Prepare terminal settings
    global stdin_fd, original_term_settings
    stdin_fd = sys.stdin.fileno()
    original_term_settings = termios.tcgetattr(stdin_fd)

    # Show initialization screen
    console.print(
        Panel(
            "[bold cyan]Meshtastic Chat Monitor[/bold cyan]\n\n"
            "Connecting to device...",
            border_style="blue",
        )
    )

    try:
        iface = meshtastic.serial_interface.SerialInterface(devPath=SERIAL_PORT)
    except Exception as e:
        console.print(f"[red bold]FATAL: Could not open serial port: {e}[/red bold]")
        sys.exit(1)

    console.print("[yellow]Initializing connection...[/yellow]")

    # Subscribe to events
    pub.subscribe(on_connection, "meshtastic.connection.established")
    pub.subscribe(on_disconnect, "meshtastic.connection.lost")
    pub.subscribe(on_receive, "meshtastic.receive")

    # Wait for connection
    time.sleep(2)

    # Confirm device is alive
    try:
        info = iface.getMyNodeInfo()
        console.print(
            f"[green]✓ Connected: {info['user']['longName']} ({info['user']['id']})[/green]"
        )
        log_system(f"Ready: {info['user']['longName']}")
    except Exception as e:
        console.print(f"[red]✗ Could not verify device: {e}[/red]")
        log_system(f"Could not verify device: {e}", error=True)

    console.print("\n[dim]Starting chat monitor...[/dim]")
    time.sleep(0.5)

    # Initialize the table
    rebuild_table()

    # Create the initial panel
    initial_panel = Panel(
        current_table,
        title="[bold blue]Meshtastic Chat Monitor[/bold blue]",
        subtitle="[dim]Press 's' to send message, 'q' to quit[/dim]",
        border_style="blue",
    )

    # Start input thread
    input_handler = threading.Thread(target=input_thread, daemon=True)
    input_handler.start()

    # Start the live display with auto_refresh disabled - we'll update manually
    try:
        with Live(initial_panel, auto_refresh=False, console=console) as live:
            live_display = live
            while True:
                # Handle queued actions from input thread
                if shutdown_event.is_set():
                    break
                if request_input_event.is_set():
                    request_input_event.clear()
                    send_message_prompt()
                time.sleep(0.1)
    except KeyboardInterrupt:
        # If Ctrl+C somehow reaches here (e.g., outside raw mode), treat as shutdown
        console.print("\n[yellow]Interrupted by user[/yellow]")
    finally:
        # Restore terminal mode
        if stdin_fd is not None and original_term_settings is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_term_settings)
            except Exception:
                pass
        if iface:
            try:
                iface.close()
            except Exception:
                pass
        console.print("[green]Disconnected. Goodbye![/green]")


if __name__ == "__main__":
    main()
