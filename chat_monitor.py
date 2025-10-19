#!/usr/bin/env python3
"""
Meshtastic Chat Monitor
A nice terminal UI for monitoring messages and sending replies.
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


def on_disconnect(topic=pub.AUTO_TOPIC):
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


def send_message():
    """Prompt user to send a message"""
    global input_mode
    input_mode = True

    if live_display:
        live_display.stop()

    console.print("\n[bold cyan]Send Message[/bold cyan]")

    # Get destination
    dest = Prompt.ask("Destination", default="^all", show_default=True)

    # Get message
    message = Prompt.ask("Message")

    if message:
        try:
            console.print(f"[yellow]Sending to {dest}...[/yellow]")
            iface.sendText(message, destinationId=dest, wantAck=False)
            log_system(f"Sent message to {dest}")
        except Exception as e:
            log_system(f"Failed to send: {e}", error=True)

    input_mode = False


def input_thread():
    """Thread to handle keyboard input"""
    import sys
    import tty
    import termios

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            ch = sys.stdin.read(1)

            if ch == "q":
                log_system("Shutting down...")
                if live_display:
                    live_display.stop()
                if iface:
                    iface.close()
                console.print("\n[green]Goodbye![/green]")
                sys.exit(0)
            elif ch == "s" and not input_mode:
                send_message()

            time.sleep(0.1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main():
    global iface, live_display, current_table

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
            # Just sleep - updates happen via update_display() when messages arrive
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    finally:
        if iface:
            iface.close()
        console.print("[green]Disconnected. Goodbye![/green]")


if __name__ == "__main__":
    main()
