#!/usr/bin/env python3
import asyncio
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

# Import modal screens
from modals import (
    PresetSelectorScreen,
    RADIO_PRESETS,
    FrequencySlotSelectorScreen,
    QuitConfirmScreen,
)

# Load CSS from external file
CSS_FILE = Path(__file__).parent / "meshtastic_tui.css"
with open(CSS_FILE, "r") as f:
    APP_CSS = f.read()

# ====== CONFIG ======
SERIAL_PORT = None  # set explicitly if needed, e.g. "/dev/ttyUSB0"
MAX_MESSAGES = 500
# ====================


class ChatMonitor(App):
    """A Textual app for monitoring Meshtastic messages."""

    TITLE = "Meshtastic Terminal"
    SUB_TITLE = "Nodes: 0"
    CSS = APP_CSS

    BINDINGS = [
        Binding("s", "send_message", "Send Message", show=True),
        Binding("ctrl+m", "change_preset", "Change Preset", show=True),
        Binding("ctrl+f", "change_frequency_slot", "Change Freq Slot", show=True),
        Binding("q", "request_quit", "Quit", show=True),
    ]

    messages: reactive[list] = reactive(list)
    input_mode: reactive[bool] = reactive(False)
    node_count: reactive[int] = reactive(0)
    channel_util: reactive[float] = reactive(0.0)
    battery_level: reactive[int] = reactive(0)
    voltage: reactive[float] = reactive(0.0)
    is_connected: reactive[bool] = reactive(False)

    def __init__(self):
        super().__init__()
        self.iface = None
        self.my_node_id = None
        self.dest_input = None
        self.message_input = None
        self.current_input_step = None  # 'dest' or 'message'
        self.known_nodes = {}  # Track nodes we've seen: {node_id: {name, last_seen}}
        self.current_preset = None  # Track current radio preset
        self.current_frequency_slot = None  # Track current frequency slot
        self.is_reconnecting = False  # Track if we're in reconnection state
        self.is_disconnecting = False  # Track if we're currently handling a disconnect
        self.auto_reconnect_enabled = True  # Enable automatic reconnection
        self.reconnect_worker = None  # Track the reconnect worker
        self.stats_worker = None  # Track the stats update worker

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
        self.update_subtitle()

    def watch_channel_util(self, channel_util: float) -> None:
        """Update subtitle when channel utilization changes."""
        self.update_subtitle()

    def watch_battery_level(self, battery_level: int) -> None:
        """Update subtitle when battery level changes."""
        self.update_subtitle()

    def watch_voltage(self, voltage: float) -> None:
        """Update subtitle when voltage changes."""
        self.update_subtitle()

    def watch_is_connected(self, is_connected: bool) -> None:
        """Update bindings when connection state changes."""
        self.refresh_bindings()

    def update_subtitle(self) -> None:
        """Update the subtitle with current stats."""
        parts = [f"Nodes: {self.node_count}"]

        if self.channel_util > 0:
            parts.append(f"ChUtil: {self.channel_util:.1f}%")

        if self.battery_level > 100:
            parts.append("PWRD")
        elif self.battery_level > 0:
            parts.append(f"Batt: {self.battery_level}%")
        elif self.voltage > 0:
            parts.append(f"Volt: {self.voltage:.2f}V")

        self.sub_title = " | ".join(parts)

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

    def check_action_state(self, action: str) -> bool:
        """Check if an action should be enabled based on connection state."""
        # Disable preset and frequency slot changes when not connected
        if action in ("change_preset", "change_frequency_slot", "send_message"):
            return self.is_connected
        # All other actions are always enabled
        return True

    def subscribe_to_events(self) -> None:
        """Subscribe to pub/sub events (unsubscribes first to avoid duplicates)."""
        try:
            # Unsubscribe first to avoid duplicate subscriptions
            pub.unsubscribe(self.on_connection, "meshtastic.connection.established")
            pub.unsubscribe(self.on_disconnect, "meshtastic.connection.lost")
            pub.unsubscribe(self.on_receive, "meshtastic.receive")
        except Exception:
            pass  # Ignore if not subscribed

        # Now subscribe
        pub.subscribe(self.on_connection, "meshtastic.connection.established")
        pub.subscribe(self.on_disconnect, "meshtastic.connection.lost")
        pub.subscribe(self.on_receive, "meshtastic.receive")

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
            self.subscribe_to_events()

            # Wait a moment for connection
            await asyncio.sleep(2)

            # Get node info
            info = await loop.run_in_executor(None, self.iface.getMyNodeInfo)
            self.log_system(f"Ready: {info['user']['longName']}")

            # Register our own node
            self.register_node(self.my_node_id, info["user"].get("longName"))

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
                            # Register node from device database
                            self.known_nodes[node_id] = {
                                "name": node_name,
                                "last_seen": datetime.now().isoformat(),
                                "first_seen": datetime.now().isoformat(),
                            }
                            node_count += 1

                    self.node_count = len(self.known_nodes)
                    self.log_system(
                        f"Loaded {node_count} node{'s' if node_count != 1 else ''} from device"
                    )
            except Exception as e:
                # Don't fail if we can't load nodes
                self.log_system("Unable to load nodes from device")
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

                        # Also log frequency slot if available
                        if hasattr(lora_config, "channel_num"):
                            channel_num = lora_config.channel_num
                            self.current_frequency_slot = channel_num
                            slot_display = (
                                f"{channel_num} (auto)"
                                if channel_num == 0
                                else str(channel_num)
                            )
                            self.log_system(f"Frequency slot: {slot_display}")

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

            # Start periodic stats update
            self.stats_worker = self.run_worker(
                self.update_stats_loop(), exclusive=False
            )

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

            # Mark as connected
            self.is_connected = True

            # Stop any auto-reconnect attempts since we're now connected
            if self.is_reconnecting:
                self.is_reconnecting = False
                if self.reconnect_worker is not None:
                    self.reconnect_worker.cancel()
                    self.reconnect_worker = None

            # Reset disconnecting flag on successful connection
            self.is_disconnecting = False

        except Exception as e:
            self.log_system(f"Connection warning: {e}")

    def on_disconnect(self, interface=None, topic=pub.AUTO_TOPIC):
        """Handle disconnection event."""
        # Prevent duplicate disconnect handling
        if self.is_disconnecting:
            return

        self.is_disconnecting = True
        self.is_connected = False  # Mark as disconnected
        self.log_system("Disconnected from device", error=True)

        # Close the existing interface to prevent automatic reconnection
        if self.iface:
            try:
                self.iface.close()
            except Exception:
                pass
            self.iface = None

        # Start auto-reconnect if enabled and not already reconnecting
        if self.auto_reconnect_enabled and not self.is_reconnecting:
            self.log_system("Will attempt to reconnect in 30 seconds...")
            self.is_reconnecting = True
            # Cancel any existing reconnect worker
            if self.reconnect_worker is not None:
                self.reconnect_worker.cancel()
            # Start new reconnect worker
            self.reconnect_worker = self.run_worker(
                self.auto_reconnect_loop(), exclusive=False
            )

        # Reset the disconnecting flag after a short delay to allow re-detection if needed
        asyncio.create_task(self._reset_disconnect_flag())

    async def _reset_disconnect_flag(self) -> None:
        """Reset the disconnecting flag after a brief delay."""
        await asyncio.sleep(2)
        self.is_disconnecting = False

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

        # Handle telemetry packets from our own node
        if portnum == "TELEMETRY_APP" and from_id == self.my_node_id:
            try:
                telemetry = decoded.get("telemetry", {})

                # Device metrics (battery, voltage, etc.)
                if "deviceMetrics" in telemetry:
                    metrics = telemetry["deviceMetrics"]
                    if "batteryLevel" in metrics and metrics["batteryLevel"] > 0:
                        self.battery_level = metrics["batteryLevel"]
                    if "voltage" in metrics and metrics["voltage"] > 0:
                        self.voltage = metrics["voltage"]
                    if "channelUtilization" in metrics:
                        self.channel_util = metrics["channelUtilization"]

                # Air quality or environment metrics if available
                # Can add more telemetry types here as needed
            except Exception:
                pass  # Ignore telemetry parsing errors

            # Return early after handling telemetry - don't process as text message
            return

        # Skip non-text message types (these are handled above or not needed)
        ignored_types = [
            "POSITION_APP",
            "TELEMETRY_APP",  # Already handled above
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

    def on_key(self, event) -> None:
        """Handle key events globally."""
        # ESC or Ctrl+C to cancel input mode
        if (event.key == "escape" or event.key == "ctrl+c") and self.input_mode:
            self.log_system("Message input cancelled")
            self.cancel_input()
            event.prevent_default()
            event.stop()

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

    def action_change_frequency_slot(self) -> None:
        """Show the frequency slot selector dialog."""
        if not self.iface:
            self.log_system("Not connected to device", error=True)
            return

        def handle_slot_selection(slot: int | None) -> None:
            """Handle the frequency slot selection from the modal."""
            if slot is not None:
                self.run_worker(self.change_frequency_slot(slot), exclusive=False)

        self.push_screen(
            FrequencySlotSelectorScreen(self.current_frequency_slot),
            handle_slot_selection,
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

    async def change_frequency_slot(self, slot: int) -> None:
        """Change the frequency slot and handle device reboot."""
        if not 0 <= slot <= 83:
            self.log_system(
                f"Invalid frequency slot: {slot} (must be 0-83)", error=True
            )
            return

        slot_display = f"{slot} (auto)" if slot == 0 else str(slot)
        self.log_system(f"Changing frequency slot to {slot_display}...")

        try:
            loop = asyncio.get_event_loop()

            # Set the frequency slot using the correct API
            def set_slot():
                try:
                    node = self.iface.localNode
                    if node:
                        # Set the channel_num value
                        node.localConfig.lora.channel_num = slot
                        # Write the config to the device
                        node.writeConfig("lora")
                        return True
                    return False
                except Exception as e:
                    raise e

            success = await loop.run_in_executor(None, set_slot)

            if success:
                slot_display = f"{slot} (auto)" if slot == 0 else str(slot)
                self.log_system(
                    f"Frequency slot changed to {slot_display}. Device will reboot..."
                )
                self.current_frequency_slot = slot
                self.is_reconnecting = True

                # Wait for device to reboot (typically takes 5-10 seconds)
                await asyncio.sleep(10)

                # Attempt to reconnect
                self.log_system("Attempting to reconnect...")
                await self.reconnect_device()
            else:
                self.log_system("Failed to change frequency slot", error=True)

        except Exception as e:
            self.log_system(f"Error changing frequency slot: {e}", error=True)
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
                self.subscribe_to_events()

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
                            # Update current frequency slot
                            if hasattr(lora_config, "channel_num"):
                                self.current_frequency_slot = lora_config.channel_num
                                self.log_system(
                                    f"Verified frequency slot: {self.current_frequency_slot}"
                                )
                except Exception:
                    pass

                self.is_reconnecting = False
                self.is_disconnecting = (
                    False  # Reset disconnect flag on successful reconnect
                )
                self.is_connected = True  # Mark as connected
                return

            except Exception as e:
                self.log_system(f"Reconnection attempt {attempt} failed: {e}")
                if attempt >= max_attempts:
                    self.log_system(
                        "Failed to reconnect. Please restart the app.", error=True
                    )
                    self.is_reconnecting = False

    async def auto_reconnect_loop(self) -> None:
        """Automatically attempt to reconnect every 30 seconds after disconnect."""
        while self.is_reconnecting and self.auto_reconnect_enabled:
            try:
                # Wait 30 seconds before attempting reconnect
                await asyncio.sleep(30)

                # Check if we should stop (might have been cancelled or connected elsewhere)
                if not self.auto_reconnect_enabled or not self.is_reconnecting:
                    break

                self.log_system("Attempting automatic reconnection...")

                # Close the old interface if it exists
                if self.iface:
                    loop = asyncio.get_event_loop()
                    try:
                        await loop.run_in_executor(None, self.iface.close)
                    except Exception:
                        pass
                    self.iface = None

                # Wait a moment
                await asyncio.sleep(2)

                # Try to reconnect
                loop = asyncio.get_event_loop()
                self.iface = await loop.run_in_executor(
                    None,
                    lambda: meshtastic.serial_interface.SerialInterface(
                        devPath=SERIAL_PORT
                    ),
                )

                # Re-subscribe to events (important!)
                pub.subscribe(self.on_connection, "meshtastic.connection.established")
                pub.subscribe(self.on_disconnect, "meshtastic.connection.lost")
                pub.subscribe(self.on_receive, "meshtastic.receive")

                # Wait for connection to stabilize
                await asyncio.sleep(3)

                # Verify connection
                info = await loop.run_in_executor(None, self.iface.getMyNodeInfo)
                self.log_system(f"Successfully reconnected: {info['user']['longName']}")

                # Update node info
                self.my_node_id = info["user"]["id"]

                # Stop reconnecting - we're connected!
                self.is_reconnecting = False
                self.is_disconnecting = (
                    False  # Reset disconnect flag on successful reconnect
                )
                self.is_connected = True  # Mark as connected
                self.reconnect_worker = None
                return

            except Exception as e:
                # Log the error and continue the loop (will try again in 30 seconds)
                self.log_system(
                    f"Reconnection failed: {e}. Will retry in 30 seconds..."
                )

    async def update_stats_loop(self) -> None:
        """Periodically request device telemetry."""
        while True:
            try:
                # Wait 30 seconds between updates
                await asyncio.sleep(30)

                # Skip if not connected
                if not self.iface or not self.iface.localNode:
                    continue

                # Request telemetry from the device
                # This triggers a TELEMETRY_APP packet that will be received
                # and processed by on_receive() to update battery/voltage/channel_util
                loop = asyncio.get_event_loop()

                def request_telemetry():
                    try:
                        # Request device metrics telemetry
                        self.iface.sendTelemetry(
                            destinationId=self.my_node_id or "^local",
                            wantResponse=False,
                            channelIndex=0,
                            telemetryType="device_metrics",
                        )
                    except Exception:
                        pass

                await loop.run_in_executor(None, request_telemetry)

            except asyncio.CancelledError:
                # Worker cancelled, exit cleanly
                break
            except Exception:
                # Ignore errors, will retry on next iteration
                pass

    async def on_shutdown(self) -> None:
        """Clean up when shutting down."""
        # Disable auto-reconnect
        self.auto_reconnect_enabled = False
        self.is_reconnecting = False

        # Cancel workers if running
        if self.reconnect_worker is not None:
            self.reconnect_worker.cancel()
            self.reconnect_worker = None

        if self.stats_worker is not None:
            self.stats_worker.cancel()
            self.stats_worker = None

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
