#!/usr/bin/env python3
"""
Meshtastic TUI
A modern terminal UI for monitoring Meshtastic messages and sending replies.

Features:
- Real-time message display with timestamps
- Node discovery tracking with persistence
- Radio configuration display (preset/region)
- Live node counter in header

Press 's' to send a message, Ctrl+Q to quit, ESC to cancel input.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
import meshtastic
import meshtastic.serial_interface
from pubsub import pub
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Grid
from textual.widgets import Header, Footer, DataTable, Input, Static, Button, Label
from textual.binding import Binding
from textual.reactive import reactive
from textual import on
from textual.screen import ModalScreen

# ====== CONFIG ======
SERIAL_PORT = None  # set explicitly if needed, e.g. "/dev/ttyUSB0"
MAX_MESSAGES = 50
NODES_FILE = Path("meshtastic_nodes.json")
# ====================

# Radio presets mapping
RADIO_PRESETS = {
    "LONG_FAST": 0,
    "LONG_SLOW": 1,
    "VERY_LONG_SLOW": 2,
    "MEDIUM_SLOW": 3,
    "MEDIUM_FAST": 4,
    "SHORT_SLOW": 5,
    "SHORT_FAST": 6,
    "LONG_MODERATE": 7,
}


class PresetSelectorScreen(ModalScreen):
    """Modal screen for selecting radio presets."""

    CSS = """
    PresetSelectorScreen {
        align: center middle;
    }

    #preset-dialog {
        width: 60;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }

    #preset-title {
        width: 100%;
        content-align: center middle;
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }

    #preset-buttons {
        width: 100%;
        height: auto;
        layout: grid;
        grid-size: 2;
        grid-gutter: 1;
        margin-top: 1;
    }

    #preset-buttons Button {
        width: 100%;
        min-height: 3;
    }

    #preset-buttons Button:focus {
        border: heavy $accent;
        background: $accent 20%;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_dialog", "Cancel"),
        ("enter", "select_button", "Select"),
        ("up", "focus_previous", "Up"),
        ("down", "focus_next", "Down"),
        ("left", "focus_previous", "Left"),
        ("right", "focus_next", "Right"),
    ]

    def __init__(self, current_preset: str = None):
        super().__init__()
        self.current_preset = current_preset
        self.button_list = []

    def compose(self) -> ComposeResult:
        """Create the preset selector dialog."""
        with Container(id="preset-dialog"):
            current_text = (
                f" (Current: {self.current_preset})" if self.current_preset else ""
            )
            yield Label(f"Select Radio Preset{current_text}", id="preset-title")
            with Grid(id="preset-buttons") as grid:
                for preset_name in RADIO_PRESETS.keys():
                    button = Button(preset_name, id=f"preset-{preset_name}")
                    button.can_focus = True
                    yield button

    def on_mount(self) -> None:
        """Focus first button when mounted."""
        # Store button list for navigation
        self.button_list = list(self.query(Button))
        if self.button_list:
            # Focus the first button
            self.set_focus(self.button_list[0])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        preset_name = event.button.id.replace("preset-", "")
        self.dismiss(preset_name)

    def action_select_button(self) -> None:
        """Select the currently focused button."""
        focused = self.focused
        if isinstance(focused, Button):
            preset_name = focused.id.replace("preset-", "")
            self.dismiss(preset_name)

    def action_focus_previous(self) -> None:
        """Move focus to the previous button."""
        if not self.button_list:
            return

        focused = self.focused
        if focused in self.button_list:
            current_index = self.button_list.index(focused)
            # Move to previous button (wrap around to end if at start)
            previous_index = (current_index - 1) % len(self.button_list)
            self.set_focus(self.button_list[previous_index])

    def action_focus_next(self) -> None:
        """Move focus to the next button."""
        if not self.button_list:
            return

        focused = self.focused
        if focused in self.button_list:
            current_index = self.button_list.index(focused)
            # Move to next button (wrap around to start if at end)
            next_index = (current_index + 1) % len(self.button_list)
            self.set_focus(self.button_list[next_index])

    def action_dismiss_dialog(self) -> None:
        """Dismiss dialog without selecting."""
        self.dismiss(None)


class QuitConfirmScreen(ModalScreen):
    """Modal screen to confirm quitting the application."""

    CSS = """
    QuitConfirmScreen {
        align: center middle;
    }

    #quit-dialog {
        width: 50;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 2;
    }

    #quit-message {
        width: 100%;
        content-align: center middle;
        text-style: bold;
        color: $warning;
        margin-bottom: 2;
    }

    #quit-buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }

    #quit-buttons Button {
        margin: 0 1;
        min-width: 10;
    }

    #quit-buttons Button:focus {
        border: heavy $accent;
        background: $accent 20%;
    }
    """

    BINDINGS = [
        ("y", "confirm_quit", "Yes"),
        ("n", "cancel_quit", "No"),
        ("escape", "cancel_quit", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        """Create the quit confirmation dialog."""
        with Container(id="quit-dialog"):
            yield Label("Are you sure you want to quit?", id="quit-message")
            with Container(id="quit-buttons"):
                yield Button("Yes (Y)", id="yes-button", variant="error")
                yield Button("No (N)", id="no-button", variant="primary")

    def on_mount(self) -> None:
        """Focus the No button by default (safer choice)."""
        no_button = self.query_one("#no-button", Button)
        self.set_focus(no_button)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "yes-button":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm_quit(self) -> None:
        """Confirm quit."""
        self.dismiss(True)

    def action_cancel_quit(self) -> None:
        """Cancel quit."""
        self.dismiss(False)


class ChatMonitor(App):
    """A Textual app for monitoring Meshtastic messages."""

    TITLE = "Richs Meshtastic Monitor"
    SUB_TITLE = "Nodes: 0"

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
    
    .node-discovery {
        color: $accent;
        text-style: italic;
    }
    """

    BINDINGS = [
        Binding("s", "send_message", "Send Message", show=True),
        Binding("ctrl+m", "change_preset", "Change Preset", show=True),
        Binding("q", "request_quit", "Quit", show=True),
    ]

    messages: reactive[list] = reactive(list)
    input_mode: reactive[bool] = reactive(False)
    node_count: reactive[int] = reactive(0)

    def __init__(self):
        super().__init__()
        self.iface = None
        self.my_node_id = None
        self.dest_input = None
        self.message_input = None
        self.current_input_step = None  # 'dest' or 'message'
        self.known_nodes = {}  # Track nodes we've seen: {node_id: {name, last_seen}}
        self.current_preset = None  # Track current radio preset
        self.is_reconnecting = False  # Track if we're in reconnection state
        self.load_known_nodes()

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header()
        yield DataTable(id="messages-table")
        with Vertical(id="input-container"):
            yield Static("", id="input-label")
            yield Input(placeholder="", id="user-input")
        yield Footer()

    def watch_node_count(self, node_count: int) -> None:
        """Update subtitle when node count changes."""
        self.sub_title = f"Nodes: {node_count}"

    def on_mount(self) -> None:
        """Set up the app when mounted."""
        # Set up the table
        table = self.query_one("#messages-table", DataTable)
        table.cursor_type = "none"
        table.zebra_stripes = True
        table.add_columns("Time", "From", "To", "Message")

        # Set initial node count (now that widgets are mounted)
        self.node_count = len(self.known_nodes)

        # Connect to device in the background
        self.run_worker(self.connect_device(), exclusive=True)

    def load_known_nodes(self) -> None:
        """Load previously seen nodes from JSON file."""
        try:
            if NODES_FILE.exists():
                with open(NODES_FILE, "r") as f:
                    self.known_nodes = json.load(f)
        except Exception as e:
            # If we can't load, start with empty dict
            self.known_nodes = {}

    def save_known_nodes(self) -> None:
        """Save known nodes to JSON file."""
        try:
            with open(NODES_FILE, "w") as f:
                json.dump(self.known_nodes, f, indent=2)
        except Exception as e:
            # Log error but don't crash
            pass

    def register_node(self, node_id: str, node_name: str = None) -> bool:
        """
        Register a node and return True if it's newly discovered.

        Args:
            node_id: The node ID (e.g., "!9e9f4220")
            node_name: Optional friendly name for the node

        Returns:
            True if this is a newly discovered node, False if already known
        """
        is_new = node_id not in self.known_nodes

        self.known_nodes[node_id] = {
            "name": node_name or node_id,
            "last_seen": datetime.now().isoformat(),
            "first_seen": self.known_nodes.get(node_id, {}).get(
                "first_seen", datetime.now().isoformat()
            ),
        }

        # Save to disk
        self.save_known_nodes()

        # Update node count
        self.node_count = len(self.known_nodes)

        return is_new

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

            # Register our own node
            self.register_node(self.my_node_id, info["user"].get("longName"))

            # Print initial node count
            initial_count = len(self.known_nodes)
            self.log_system(
                f"Tracking {initial_count} node{'s' if initial_count != 1 else ''}"
            )

            # Load existing nodes from the device's node database
            try:
                if hasattr(self.iface, "nodes") and self.iface.nodes:
                    node_count = 0
                    for node_id, node_info in self.iface.nodes.items():
                        if node_id != self.my_node_id:  # Skip our own node
                            user_info = node_info.get("user", {})
                            node_name = (
                                user_info.get("longName")
                                or user_info.get("shortName")
                                or node_id
                            )
                            # Register without triggering discovery event
                            if node_id not in self.known_nodes:
                                self.known_nodes[node_id] = {
                                    "name": node_name,
                                    "last_seen": datetime.now().isoformat(),
                                    "first_seen": datetime.now().isoformat(),
                                }
                                node_count += 1
                    if node_count > 0:
                        self.save_known_nodes()
                        self.node_count = len(self.known_nodes)
                        self.log_system(f"Loaded {node_count} known nodes from device")
            except Exception as e:
                # Don't fail if we can't load nodes
                pass

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
                            self.current_preset = preset_name
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

        # Check for new nodes from any packet type
        from_id = packet.get("fromId", packet.get("from"))
        if from_id and from_id != self.my_node_id:
            # Try to get node name from the interface's node database
            node_name = None
            if hasattr(self.iface, "nodes") and from_id in self.iface.nodes:
                user_info = self.iface.nodes[from_id].get("user", {})
                node_name = user_info.get("longName") or user_info.get("shortName")

            # Register and check if new
            is_new = self.register_node(from_id, node_name)
            if is_new:
                display_name = node_name or from_id
                self.log_node_discovery(from_id, display_name)

        # Skip non-text message types
        ignored_types = [
            "POSITION_APP",
            "TELEMETRY_APP",
            "ROUTING_APP",
            "ADMIN_APP",
            "unknown",
        ]

        # Handle NODEINFO_APP specially - it means we learned about a node
        if portnum == "NODEINFO_APP":
            # We already handled this above, just return
            return

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

    def log_node_discovery(self, node_id: str, node_name: str):
        """Add a node discovery event to the table."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        table = self.query_one("#messages-table", DataTable)

        table.add_row(timestamp, "[NODE]", node_id, f"Discovered: {node_name}")

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

    def action_change_preset(self) -> None:
        """Show the preset selector dialog."""
        if not self.iface:
            self.log_system("Not connected to device", error=True)
            return

        def handle_preset_selection(preset_name: str | None) -> None:
            """Handle the preset selection from the modal."""
            if preset_name:
                self.run_worker(self.change_radio_preset(preset_name), exclusive=False)

        self.push_screen(
            PresetSelectorScreen(self.current_preset), handle_preset_selection
        )

    def action_request_quit(self) -> None:
        """Show quit confirmation dialog."""

        def handle_quit_response(confirmed: bool) -> None:
            """Handle the quit confirmation response."""
            if confirmed:
                self.exit()

        self.push_screen(QuitConfirmScreen(), handle_quit_response)

    async def change_radio_preset(self, preset_name: str) -> None:
        """Change the radio preset and handle device reboot."""
        if preset_name not in RADIO_PRESETS:
            self.log_system(f"Invalid preset: {preset_name}", error=True)
            return

        preset_value = RADIO_PRESETS[preset_name]
        self.log_system(f"Changing radio preset to {preset_name}...")

        try:
            loop = asyncio.get_event_loop()

            # Set the preset using the correct API
            def set_preset():
                try:
                    node = self.iface.localNode
                    if node:
                        # Ensure we have the lora config
                        if len(node.localConfig.ListFields()) == 0:
                            node.requestConfig(
                                node.localConfig.DESCRIPTOR.fields_by_name.get("lora")
                            )
                        # Set the modem preset value
                        node.localConfig.lora.modem_preset = preset_value
                        # Write the config to the device
                        node.writeConfig("lora")
                        return True
                    return False
                except Exception as e:
                    raise e

            success = await loop.run_in_executor(None, set_preset)

            if success:
                self.log_system(
                    f"Preset changed to {preset_name}. Device will reboot..."
                )
                self.current_preset = preset_name
                self.is_reconnecting = True

                # Wait for device to reboot (typically takes 5-10 seconds)
                await asyncio.sleep(10)

                # Attempt to reconnect
                self.log_system("Attempting to reconnect...")
                await self.reconnect_device()
            else:
                self.log_system("Failed to change preset", error=True)

        except Exception as e:
            self.log_system(f"Error changing preset: {e}", error=True)
            self.is_reconnecting = False

    async def reconnect_device(self) -> None:
        """Reconnect to the device after a reboot."""
        max_attempts = 5
        attempt = 0

        while attempt < max_attempts and self.is_reconnecting:
            attempt += 1
            self.log_system(f"Reconnection attempt {attempt}/{max_attempts}...")

            try:
                # Close the old interface
                if self.iface:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self.iface.close)
                    self.iface = None

                # Wait a bit before trying to reconnect
                await asyncio.sleep(3)

                # Try to reconnect
                loop = asyncio.get_event_loop()
                self.iface = await loop.run_in_executor(
                    None,
                    lambda: meshtastic.serial_interface.SerialInterface(
                        devPath=SERIAL_PORT
                    ),
                )

                # Re-subscribe to events
                pub.subscribe(self.on_connection, "meshtastic.connection.established")
                pub.subscribe(self.on_disconnect, "meshtastic.connection.lost")
                pub.subscribe(self.on_receive, "meshtastic.receive")

                # Wait for connection to stabilize
                await asyncio.sleep(2)

                # Verify connection
                info = await loop.run_in_executor(None, self.iface.getMyNodeInfo)
                self.log_system(f"Reconnected: {info['user']['longName']}")

                # Update current preset
                try:
                    if hasattr(self.iface, "localNode") and self.iface.localNode:
                        local_config = self.iface.localNode.localConfig
                        if local_config and hasattr(local_config, "lora"):
                            lora_config = local_config.lora
                            if hasattr(lora_config, "modem_preset"):
                                preset_value = lora_config.modem_preset
                                preset_names = {v: k for k, v in RADIO_PRESETS.items()}
                                if hasattr(preset_value, "name"):
                                    self.current_preset = preset_value.name
                                else:
                                    self.current_preset = preset_names.get(preset_value)
                                self.log_system(
                                    f"Verified preset: {self.current_preset}"
                                )
                except Exception:
                    pass

                self.is_reconnecting = False
                return

            except Exception as e:
                self.log_system(f"Reconnection attempt {attempt} failed: {e}")
                if attempt >= max_attempts:
                    self.log_system(
                        "Failed to reconnect. Please restart the app.", error=True
                    )
                    self.is_reconnecting = False

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
