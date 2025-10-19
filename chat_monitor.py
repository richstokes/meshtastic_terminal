#!/usr/bin/env python3
"""
Meshtastic Chat Monitor
A terminal UI for monitoring messages and sending replies.
Press 's' to send a message, Ctrl+Q to quit.
"""
import asyncio
from datetime import datetime
from typing import Optional
import meshtastic
import meshtastic.serial_interface
from pubsub import pub
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Header, Footer, DataTable, Input, Static
from textual.binding import Binding
from textual.reactive import reactive
from textual import on

# ====== CONFIG ======
SERIAL_PORT = None  # set explicitly if needed, e.g. "/dev/ttyUSB0"
MAX_MESSAGES = 50
# ====================


class ChatMonitor(App):
    """A Textual app for monitoring Meshtastic messages."""

    TITLE = "Richs Meshtastic Monitor"

    CSS = """
    Screen {
        background: $background;
    }

    DataTable {
        height: 1fr;
        border: solid $primary;
    }

    #input-container {
        height: auto;
        display: none;
        border: solid $accent;
        padding: 1;
    }

    #input-container.visible {
        display: block;
    }

    Input {
        margin: 0 1;
    }

    .system-message {
        color: $warning;
    }

    .error-message {
        color: $error;
    }

    .from-me {
        color: $success;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("s", "send_message", "Send Message", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    messages: reactive[list] = reactive(list)
    input_mode: reactive[bool] = reactive(False)

    def __init__(self):
        super().__init__()
        self.iface = None
        self.my_node_id = None
        self.dest_input = None
        self.message_input = None
        self.current_input_step = None  # 'dest' or 'message'

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header()
        yield DataTable(id="messages-table")
        with Vertical(id="input-container"):
            yield Static("", id="input-label")
            yield Input(placeholder="", id="user-input")
        yield Footer()

    def on_mount(self) -> None:
        """Set up the app when mounted."""
        # Set up the table
        table = self.query_one("#messages-table", DataTable)
        table.cursor_type = "none"
        table.zebra_stripes = True
        table.add_columns("Time", "From", "To", "Message")

        # Connect to device in the background
        self.run_worker(self.connect_device(), exclusive=True)

    async def connect_device(self) -> None:
        """Connect to Meshtastic device."""
        self.log_system("Connecting to device...")

        try:
            # Run blocking meshtastic operations in executor
            loop = asyncio.get_event_loop()
            self.iface = await loop.run_in_executor(
                None,
                lambda: meshtastic.serial_interface.SerialInterface(
                    devPath=SERIAL_PORT
                ),
            )

            self.log_system("Initializing connection...")

            # Subscribe to events
            pub.subscribe(self.on_connection, "meshtastic.connection.established")
            pub.subscribe(self.on_disconnect, "meshtastic.connection.lost")
            pub.subscribe(self.on_receive, "meshtastic.receive")

            # Wait a moment for connection
            await asyncio.sleep(2)

            # Get node info
            info = await loop.run_in_executor(None, self.iface.getMyNodeInfo)
            self.log_system(f"Ready: {info['user']['longName']}")

            # Log radio configuration
            try:
                # Get the modem preset/config
                if hasattr(self.iface, "localNode") and self.iface.localNode:
                    local_config = self.iface.localNode.localConfig
                    if local_config and hasattr(local_config, "lora"):
                        lora_config = local_config.lora
                        if hasattr(lora_config, "modem_preset"):
                            preset_value = lora_config.modem_preset
                            # Map numeric preset to friendly name
                            preset_names = {
                                0: "LONG_FAST",
                                1: "LONG_SLOW",
                                2: "VERY_LONG_SLOW",
                                3: "MEDIUM_SLOW",
                                4: "MEDIUM_FAST",
                                5: "SHORT_SLOW",
                                6: "SHORT_FAST",
                                7: "LONG_MODERATE",
                            }
                            # Try to get name attribute first, otherwise use mapping
                            if hasattr(preset_value, "name"):
                                preset_name = preset_value.name
                            else:
                                preset_name = preset_names.get(
                                    preset_value, f"Unknown ({preset_value})"
                                )
                            self.log_system(f"Radio preset: {preset_name}")

                        # Also log region if available
                        if hasattr(lora_config, "region"):
                            region_value = lora_config.region
                            # Map numeric region to friendly name
                            region_names = {
                                0: "UNSET",
                                1: "US",
                                2: "EU_433",
                                3: "EU_868",
                                4: "CN",
                                5: "JP",
                                6: "ANZ",
                                7: "KR",
                                8: "TW",
                                9: "RU",
                                10: "IN",
                                11: "NZ_865",
                                12: "TH",
                                13: "UA_433",
                                14: "UA_868",
                                15: "MY_433",
                                16: "MY_919",
                                17: "SG_923",
                            }
                            # Try to get name attribute first, otherwise use mapping
                            if hasattr(region_value, "name"):
                                region_name = region_value.name
                            else:
                                region_name = region_names.get(
                                    region_value, f"Unknown ({region_value})"
                                )
                            self.log_system(f"Region: {region_name}")
            except Exception as e:
                # Don't fail if we can't get radio config
                pass

        except Exception as e:
            self.log_system(f"FATAL: Could not connect: {e}", error=True)

    def on_connection(self, interface, topic=pub.AUTO_TOPIC):
        """Handle connection event."""
        try:
            node = interface.getMyNodeInfo()
            self.my_node_id = node["user"]["id"]
            self.log_system(
                f"Connected: {node['user']['shortName']} ({self.my_node_id})"
            )
        except Exception as e:
            self.log_system(f"Connection warning: {e}")

    def on_disconnect(self, interface=None, topic=pub.AUTO_TOPIC):
        """Handle disconnection event."""
        self.log_system("Disconnected from device", error=True)

    def on_receive(self, packet, interface):
        """Monitor received packets."""
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
                self.log_message(from_id, to_id, message_content)

    def log_message(self, from_id: str, to_id: str, content: str):
        """Add a message to the table."""
        if not content or not content.strip():
            return

        timestamp = datetime.now().strftime("%H:%M:%S")

        table = self.query_one("#messages-table", DataTable)

        # Apply styling based on sender
        from_style = "from-me" if from_id == self.my_node_id else ""
        to_style = "from-me" if to_id == self.my_node_id else ""

        table.add_row(timestamp, from_id, to_id, content)

        # Keep only last MAX_MESSAGES
        if table.row_count > MAX_MESSAGES:
            table.remove_row(table.rows[0].key)

        # Scroll to bottom
        table.scroll_end(animate=False)

    def log_system(self, message: str, error: bool = False):
        """Add a system message to the table."""
        if not message or not message.strip():
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        table = self.query_one("#messages-table", DataTable)

        style_class = "error-message" if error else "system-message"

        table.add_row(timestamp, "[SYSTEM]", "", message)

        # Keep only last MAX_MESSAGES
        if table.row_count > MAX_MESSAGES:
            table.remove_row(table.rows[0].key)

        # Scroll to bottom
        table.scroll_end(animate=False)

    def action_send_message(self) -> None:
        """Start the message sending flow."""
        if self.input_mode:
            return

        self.input_mode = True
        self.current_input_step = "dest"

        # Show input container
        container = self.query_one("#input-container")
        container.add_class("visible")

        # Update label and focus input
        label = self.query_one("#input-label", Static)
        label.update("Destination [^all]:")

        input_widget = self.query_one("#user-input", Input)
        input_widget.value = ""
        input_widget.placeholder = "^all"
        input_widget.focus()

    @on(Input.Submitted, "#user-input")
    def handle_input_submit(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        if not self.input_mode:
            return

        value = event.value.strip()

        if self.current_input_step == "dest":
            # Save destination and move to message
            self.dest_input = value if value else "^all"
            self.current_input_step = "message"

            label = self.query_one("#input-label", Static)
            label.update("Message:")

            input_widget = self.query_one("#user-input", Input)
            input_widget.value = ""
            input_widget.placeholder = "Type your message..."

        elif self.current_input_step == "message":
            # Send the message
            self.message_input = value

            if self.message_input:
                # Run async send in background
                self.run_worker(
                    self.send_text_message(self.dest_input, self.message_input),
                    exclusive=False,
                )
            else:
                self.log_system("Message cancelled (empty)")

            self.cancel_input()

    async def send_text_message(self, dest: str, message: str) -> None:
        """Send a text message."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.iface.sendText(message, destinationId=dest, wantAck=False),
            )
            # Log the sent message in the stream as if it was a regular message
            self.log_message(self.my_node_id or "You", dest, message)
        except Exception as e:
            self.log_system(f"Failed to send: {e}", error=True)

    def cancel_input(self) -> None:
        """Cancel input mode."""
        self.input_mode = False
        self.current_input_step = None
        self.dest_input = None
        self.message_input = None

        # Hide input container
        container = self.query_one("#input-container")
        container.remove_class("visible")

        # Clear input
        input_widget = self.query_one("#user-input", Input)
        input_widget.value = ""

    def on_input_key(self, event) -> None:
        """Handle key events in input mode."""
        # ESC to cancel
        if event.key == "escape" and self.input_mode:
            self.log_system("Message input cancelled")
            self.cancel_input()

    async def on_shutdown(self) -> None:
        """Clean up when shutting down."""
        # Unsubscribe from events
        try:
            pub.unsubscribe(self.on_connection, "meshtastic.connection.established")
            pub.unsubscribe(self.on_disconnect, "meshtastic.connection.lost")
            pub.unsubscribe(self.on_receive, "meshtastic.receive")
        except Exception:
            pass

        # Close interface
        if self.iface:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.iface.close)
            except Exception:
                pass


def main():
    """Run the app."""
    app = ChatMonitor()
    app.run()


if __name__ == "__main__":
    main()
