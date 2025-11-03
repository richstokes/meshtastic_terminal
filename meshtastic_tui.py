#!/usr/bin/env python3
import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
import meshtastic
import meshtastic.serial_interface
import serial.tools.list_ports
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
    UserNameSetterScreen,
    UserSelectorScreen,
    SerialPortSelectorScreen,
    NodeListScreen,
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
        Binding("d", "direct_message", "Direct Message", show=True),
        Binding("h", "toggle_hop_column", "Toggle Hops", show=True),
        Binding("ctrl+n", "show_node_list", "Node List", show=True),
        Binding("ctrl+m", "change_preset", "Change Preset", show=True),
        Binding("ctrl+f", "change_frequency_slot", "Change Freq Slot", show=True),
        Binding("ctrl+u", "set_user_name", "Set User Name", show=True),
        Binding("q", "request_quit", "Quit", show=True),
    ]

    messages: reactive[list] = reactive(list)
    input_mode: reactive[bool] = reactive(False)
    node_count: reactive[int] = reactive(0)
    channel_util: reactive[float] = reactive(0.0)
    battery_level: reactive[int] = reactive(0)
    voltage: reactive[float] = reactive(0.0)
    is_connected: reactive[bool] = reactive(False)
    show_hop_column: reactive[bool] = reactive(False)

    def __init__(self, auto_connect: bool = False):
        super().__init__()
        self.iface = None
        self.my_node_id = None
        self.dest_input = None
        self.message_input = None
        self.current_input_step = None  # 'dest' or 'message'
        self.known_nodes = {}  # Track nodes we've seen: {node_id: {name, last_seen}}
        self.current_preset = None  # Track current radio preset
        self.current_frequency_slot = None  # Track current frequency slot
        self.current_long_name = ""  # Track current long name
        self.current_short_name = ""  # Track current short name
        self.is_reconnecting = False  # Track if we're in reconnection state
        self.is_disconnecting = False  # Track if we're currently handling a disconnect
        self.auto_reconnect_enabled = True  # Enable automatic reconnection
        self.reconnect_worker = None  # Track the reconnect worker
        self.stats_worker = None  # Track the stats update worker
        self.last_packet_received = None  # Track last time we received any packet
        self.selected_serial_port = None  # Track the selected serial port
        self.auto_connect = auto_connect  # Whether to auto-connect to first port
        self.message_metadata = []  # Store full message data including hop counts

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

    def _setup_table_columns(self) -> None:
        """Set up table columns based on show_hop_column state."""
        table = self.query_one("#messages-table", DataTable)
        table.clear(columns=True)
        
        if self.show_hop_column:
            table.add_columns("Time", "From", "To", "Hops", "Message")
        else:
            table.add_columns("Time", "From", "To", "Message")

    def on_mount(self) -> None:
        """Set up the app when mounted."""
        # Set up the table
        table = self.query_one("#messages-table", DataTable)
        table.cursor_type = "none"
        table.zebra_stripes = True
        self._setup_table_columns()

        # Set initial node count (now that widgets are mounted)
        self.node_count = len(self.known_nodes)

        # Auto-connect or show port selector
        if self.auto_connect:
            self.auto_connect_first_port()
        else:
            self.show_port_selector()

    def check_action_state(self, action: str) -> bool:
        """Check if an action should be enabled based on connection state."""
        # Disable preset and frequency slot changes when not connected
        if action in (
            "change_preset",
            "change_frequency_slot",
            "send_message",
            "direct_message",
            "set_user_name",
            "show_node_list",
        ):
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

    def _normalize_node_id(self, packet) -> Optional[str]:
        """
        Extract and normalize node ID from packet to string format.
        
        Args:
            packet: The packet dictionary containing 'fromId'/'from' or 'toId'/'to'
            
        Returns:
            Normalized string ID (e.g., "!9bb3634b") or None if not found
        """
        # Try string ID first (fromId or toId)
        node_id = packet.get("fromId") or packet.get("toId")
        if node_id:
            return node_id
        
        # Fall back to numeric ID (from or to)
        node_num = packet.get("from") or packet.get("to")
        if node_num:
            # Try to look up in nodesByNum for proper ID
            if hasattr(self.iface, "nodesByNum") and node_num in self.iface.nodesByNum:
                node_info = self.iface.nodesByNum[node_num]
                return node_info.get("user", {}).get("id") or f"!{node_num:08x}"
            else:
                return f"!{node_num:08x}"
        
        return None

    def get_node_display_name(self, node_id: str, use_cache: bool = True) -> str:
        """Get a friendly display name for a node.
        
        This method centralizes the name resolution logic:
        1. Check known_nodes cache (if use_cache=True)
        2. Fallback to iface.nodes database
        3. Cache newly found names
        4. Return node_id if no friendly name found
        
        Args:
            node_id: The node ID to get display name for
            use_cache: Whether to check and update the cache (default True)
            
        Returns:
            The friendly display name or node_id if not found
        """
        if not node_id:
            return "unknown"
            
        # Try cache first if enabled
        if use_cache:
            cached_name = self.known_nodes.get(node_id, {}).get("name")
            if cached_name and cached_name != node_id:
                return cached_name
        
        # Fallback to interface's node database
        friendly_name = None
        if hasattr(self.iface, "nodes") and self.iface and node_id in self.iface.nodes:
            user_info = self.iface.nodes[node_id].get("user", {})
            friendly_name = user_info.get("longName") or user_info.get("shortName")
            
            # Cache it for future lookups
            if friendly_name and use_cache:
                self.register_node(node_id, friendly_name)
        
        return friendly_name or node_id

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
        
        # Determine the best name to use
        # Priority: new non-trivial name > existing name > node_id
        existing_name = self.known_nodes.get(node_id, {}).get("name")
        
        # Only update name if new name is better than existing
        if node_name and node_name != node_id:
            # New name is good, use it
            best_name = node_name
        elif existing_name and existing_name != node_id:
            # Keep existing good name
            best_name = existing_name
        else:
            # Fall back to node_id
            best_name = node_id

        self.known_nodes[node_id] = {
            "name": best_name,
            "last_seen": datetime.now().isoformat(),
            "first_seen": self.known_nodes.get(node_id, {}).get(
                "first_seen", datetime.now().isoformat()
            ),
        }

        # Update node count
        self.node_count = len(self.known_nodes)

        return is_new

    def auto_connect_first_port(self) -> None:
        """Auto-connect to the first available serial port."""
        # Get available serial ports
        ports = serial.tools.list_ports.comports()
        
        # Filter out typical non-device ports
        filtered_ports = [
            p for p in ports 
            if not any(skip in p.device.lower() for skip in ['bluetooth', 'debug'])
        ]
        
        if filtered_ports:
            # Use the first available port
            self.selected_serial_port = filtered_ports[0].device
            self.log_system(f"Auto-connecting to first port: {self.selected_serial_port}")
            # Connect to device in the background
            self.run_worker(self.connect_device(), exclusive=True)
        else:
            # No ports found, fall back to auto-detect
            self.log_system("No serial ports found, using auto-detect")
            self.selected_serial_port = None
            self.run_worker(self.connect_device(), exclusive=True)

    def show_port_selector(self) -> None:
        """Show serial port selector dialog on launch."""
        def handle_port_selection(selected_port) -> None:
            """Handle port selection from the modal."""
            if selected_port is False:
                # User cancelled
                self.log_system("Connection cancelled by user", error=True)
                self.exit()
            else:
                # selected_port can be None (auto-detect) or a device path
                self.selected_serial_port = selected_port
                # Connect to device in the background
                self.run_worker(self.connect_device(), exclusive=True)

        self.push_screen(SerialPortSelectorScreen(), handle_port_selection)

    async def connect_device(self) -> None:
        """Connect to Meshtastic device."""
        if self.selected_serial_port:
            self.log_system(f"Connecting to {self.selected_serial_port}...")
        else:
            self.log_system("Connecting to device (auto-detect)...")

        try:
            # Run blocking meshtastic operations in executor
            loop = asyncio.get_event_loop()
            self.iface = await loop.run_in_executor(
                None,
                lambda: meshtastic.serial_interface.SerialInterface(
                    devPath=self.selected_serial_port
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

            # Store our node ID early (needed for registration logic)
            self.my_node_id = info["user"]["id"]
            
            # Store current user names
            self.current_long_name = info["user"].get("longName", "")
            self.current_short_name = info["user"].get("shortName", "")

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
                            )
                            # Register node from device database using consistent method
                            self.register_node(node_id, node_name)
                            node_count += 1

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
                    
                    # Log device role/mode
                    if local_config and hasattr(local_config, "device"):
                        device_config = local_config.device
                        if hasattr(device_config, "role"):
                            role_value = device_config.role
                            # Map numeric role to friendly name
                            role_names = {
                                0: "CLIENT",
                                1: "CLIENT_MUTE",
                                2: "ROUTER",
                                3: "ROUTER_CLIENT",
                                4: "REPEATER",
                                5: "TRACKER",
                                6: "SENSOR",
                                7: "TAK",
                                8: "CLIENT_HIDDEN",
                                9: "LOST_AND_FOUND",
                                10: "TAK_TRACKER",
                            }
                            # Try to get name attribute first, otherwise use mapping
                            if hasattr(role_value, "name"):
                                role_name = role_value.name
                            else:
                                role_name = role_names.get(
                                    role_value, f"Unknown ({role_value})"
                                )
                            self.log_system(f"Device mode: {role_name}")
                    
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
            self.log_system("Will attempt to reconnect in 15 seconds...")
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
        # Update last packet timestamp for connection health monitoring
        self.last_packet_received = datetime.now()
        
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum", "unknown")

        # Check for new nodes from sender only (not destination)
        from_id = self._normalize_node_id({"fromId": packet.get("fromId"), "from": packet.get("from")})
        
        if from_id and from_id != self.my_node_id and not from_id.startswith("^"):
            # Get node name and register if new (exclude channel names like ^all)
            node_name = self.get_node_display_name(from_id)
            is_new = self.register_node(from_id, node_name if node_name != from_id else None)
            # Only log discovery if we have a friendly name (not just the node ID)
            if is_new and node_name != from_id:
                self.log_node_discovery(from_id, node_name)

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

        # Handle NODEINFO_APP specially - this contains node name information
        if portnum == "NODEINFO_APP":
            # NODEINFO packets are the ideal time to discover nodes with friendly names
            # The meshtastic library updates iface.nodes automatically, so we can now
            # get the friendly name and log discovery if this is a new node
            if from_id and from_id != self.my_node_id and not from_id.startswith("^"):
                # Get the friendly name (should be available now after NODEINFO)
                node_name = self.get_node_display_name(from_id)
                
                # Check if this was previously known only by ID
                was_unknown = from_id not in self.known_nodes
                previously_unnamed = (
                    from_id in self.known_nodes 
                    and self.known_nodes[from_id].get("name") == from_id
                )
                
                # Register/update the node
                self.register_node(from_id, node_name if node_name != from_id else None)
                
                # Log discovery if new or if we just learned the name
                if (was_unknown or previously_unnamed) and node_name != from_id:
                    self.log_node_discovery(from_id, node_name)
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
                # Extract and normalize sender/receiver IDs
                msg_from_id = self._normalize_node_id({"fromId": packet.get("fromId"), "from": packet.get("from")}) or "unknown"
                msg_to_id = self._normalize_node_id({"toId": packet.get("toId"), "to": packet.get("to")}) or "unknown"
                
                # Check if this is a reply (has replyId field)
                reply_id = decoded.get("replyId") or packet.get("replyId")
                is_reply = reply_id is not None and reply_id != 0
                
                # Extract hop count (hopLimit - current hopStart gives hops taken)
                hop_limit = packet.get("hopLimit", 0)
                hop_start = packet.get("hopStart", hop_limit)
                hops_taken = hop_start - hop_limit if hop_start >= hop_limit else 0
                
                self.log_message(msg_from_id, msg_to_id, message_content, is_reply=is_reply, hop_count=hops_taken)

    def log_message(self, from_id: str, to_id: str, content: str, is_reply: bool = False, hop_count: int = 0):
        """Add a message to the table."""
        if not content or not content.strip():
            return

        timestamp = datetime.now().strftime("%H:%M:%S")

        table = self.query_one("#messages-table", DataTable)

        # Apply styling based on sender
        from_style = "from-me" if from_id == self.my_node_id else ""
        to_style = "from-me" if to_id == self.my_node_id else ""

        # Get display names using centralized helper
        from_display = self.get_node_display_name(from_id)
        to_display = self.get_node_display_name(to_id)

        # Add reply indicator if this is a reply
        # Using ↩ (U+21A9 LEFTWARDS ARROW WITH HOOK) as reply icon
        if is_reply:
            content = "↩ " + content

        # Store complete metadata (always preserve hop count)
        self.message_metadata.append({
            "timestamp": timestamp,
            "from": from_display,
            "to": to_display,
            "hops": str(hop_count),
            "message": content,
        })

        # Add row with or without hop count based on column visibility
        if self.show_hop_column:
            table.add_row(timestamp, from_display, to_display, str(hop_count), content)
        else:
            table.add_row(timestamp, from_display, to_display, content)

        # Keep only last MAX_MESSAGES
        if table.row_count > MAX_MESSAGES:
            table.remove_row(table.rows[0].key)
            self.message_metadata.pop(0)

        # Scroll to bottom
        table.scroll_end(animate=False)

    def log_system(self, message: str, error: bool = False):
        """Add a system message to the table."""
        if not message or not message.strip():
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        table = self.query_one("#messages-table", DataTable)

        style_class = "error-message" if error else "system-message"

        # Store complete metadata
        self.message_metadata.append({
            "timestamp": timestamp,
            "from": "[SYSTEM]",
            "to": "",
            "hops": "",
            "message": message,
        })

        # Add row with or without hop count based on column visibility
        if self.show_hop_column:
            table.add_row(timestamp, "[SYSTEM]", "", "", message)
        else:
            table.add_row(timestamp, "[SYSTEM]", "", message)

        # Keep only last MAX_MESSAGES
        if table.row_count > MAX_MESSAGES:
            table.remove_row(table.rows[0].key)
            self.message_metadata.pop(0)

        # Scroll to bottom
        table.scroll_end(animate=False)

    def log_node_discovery(self, node_id: str, node_name: str):
        """Add a node discovery event to the table."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        table = self.query_one("#messages-table", DataTable)

        # Show both friendly name and node ID for clarity
        if node_name and node_name != node_id:
            message = f"Discovered: {node_name} ({node_id})"
        else:
            message = f"Discovered: {node_id}"
        
        # Store complete metadata
        self.message_metadata.append({
            "timestamp": timestamp,
            "from": "[NODE]",
            "to": node_id,
            "hops": "",
            "message": message,
        })
        
        # Add row with or without hop count based on column visibility
        if self.show_hop_column:
            table.add_row(timestamp, "[NODE]", node_id, "", message)
        else:
            table.add_row(timestamp, "[NODE]", node_id, message)

        # Keep only last MAX_MESSAGES
        if table.row_count > MAX_MESSAGES:
            table.remove_row(table.rows[0].key)
            self.message_metadata.pop(0)

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

    def action_direct_message(self) -> None:
        """Show user selector dialog for direct messaging."""
        if not self.iface:
            self.log_system("Not connected to device", error=True)
            return

        # Check if we have any users to message
        if len(self.known_nodes) <= 1:  # Only ourselves or empty
            self.log_system("No other users available to message", error=True)
            return

        def handle_user_selection(selected_node_id: str | None) -> None:
            """Handle user selection from the modal."""
            if selected_node_id:
                # Start message input flow with pre-selected destination
                self.start_direct_message_input(selected_node_id)

        self.push_screen(
            UserSelectorScreen(self.known_nodes, self.my_node_id),
            handle_user_selection,
        )

    def start_direct_message_input(self, dest_node_id: str) -> None:
        """Start message input with a pre-selected destination."""
        if self.input_mode:
            return

        self.input_mode = True
        self.current_input_step = "message"  # Skip destination step
        self.dest_input = dest_node_id  # Pre-set destination

        # Show input container
        container = self.query_one("#input-container")
        container.add_class("visible")

        # Get display name for the label
        dest_name = self.known_nodes.get(dest_node_id, {}).get("name", dest_node_id)

        # Update label and focus input
        label = self.query_one("#input-label", Static)
        label.update(f"Message to {dest_name}:")

        input_widget = self.query_one("#user-input", Input)
        input_widget.value = ""
        input_widget.placeholder = "Type your message..."
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

    def action_toggle_hop_column(self) -> None:
        """Toggle the visibility of the hop count column."""
        table = self.query_one("#messages-table", DataTable)
        
        # Toggle the state
        self.show_hop_column = not self.show_hop_column
        
        # Rebuild columns
        self._setup_table_columns()
        
        # Restore all messages from metadata (which always has hop counts)
        for msg in self.message_metadata:
            if self.show_hop_column:
                table.add_row(msg["timestamp"], msg["from"], msg["to"], msg["hops"], msg["message"])
            else:
                table.add_row(msg["timestamp"], msg["from"], msg["to"], msg["message"])
        
        status = "shown" if self.show_hop_column else "hidden"
        self.log_system(f"Hop count column {status}")

    def action_show_node_list(self) -> None:
        """Show the node list dialog."""
        if not self.iface:
            self.log_system("Not connected to device", error=True)
            return

        self.push_screen(NodeListScreen(self.iface, self.my_node_id))

    def action_set_user_name(self) -> None:
        """Show the user name setter dialog."""
        if not self.iface:
            self.log_system("Not connected to device", error=True)
            return

        def handle_user_name_response(result: tuple | None) -> None:
            """Handle the user name setter response."""
            if result:
                long_name, short_name = result
                self.run_worker(
                    self.set_user_names(long_name, short_name), exclusive=False
                )

        self.push_screen(
            UserNameSetterScreen(self.current_long_name, self.current_short_name),
            handle_user_name_response,
        )

    async def set_user_names(self, long_name: str, short_name: str) -> None:
        """Set the user long name and short name."""
        if not long_name and not short_name:
            self.log_system("Both names are empty, no changes made")
            return

        self.log_system(f"Setting user names...")

        try:
            loop = asyncio.get_event_loop()

            # Set the user names using the correct API
            def set_names():
                try:
                    node = self.iface.localNode
                    if node:
                        # Use setOwner method to set both long and short names
                        node.setOwner(
                            long_name=long_name if long_name else None,
                            short_name=short_name if short_name else None,
                        )
                        return True
                    return False
                except Exception as e:
                    raise e

            success = await loop.run_in_executor(None, set_names)

            if success:
                # Update our stored values
                if long_name:
                    self.current_long_name = long_name
                if short_name:
                    self.current_short_name = short_name

                display_parts = []
                if long_name:
                    display_parts.append(f"Long: '{long_name}'")
                if short_name:
                    display_parts.append(f"Short: '{short_name}'")

                self.log_system(f"User names updated: {', '.join(display_parts)}")
                self.log_system("Device will reboot to apply changes...")
            else:
                self.log_system("Failed to set user names", error=True)

        except Exception as e:
            self.log_system(f"Error setting user names: {e}", error=True)

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
                        devPath=self.selected_serial_port
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
        """Automatically attempt to reconnect every 15 seconds after disconnect."""
        while self.is_reconnecting and self.auto_reconnect_enabled:
            try:
                # Wait 15 seconds before attempting reconnect
                await asyncio.sleep(15)

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
                        devPath=self.selected_serial_port
                    ),
                )

                # Re-subscribe to events (important!)
                self.subscribe_to_events()

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
                # Log the error and continue the loop (will try again in 15 seconds)
                self.log_system(
                    f"Reconnection failed: {e}. Will retry in 15 seconds..."
                )

    async def update_stats_loop(self) -> None:
        """Periodically request device telemetry and monitor connection health."""
        # Stale connection timeout: 5 minutes without any packets
        stale_timeout_seconds = 300
        
        while True:
            try:
                # Wait 30 seconds between updates
                await asyncio.sleep(30)

                # Skip if not connected or already reconnecting
                if not self.iface or not self.is_connected or self.is_reconnecting:
                    continue

                # Check if connection has gone stale (no packets received recently)
                if self.last_packet_received:
                    time_since_last_packet = (datetime.now() - self.last_packet_received).total_seconds()
                    
                    if time_since_last_packet > stale_timeout_seconds:
                        self.log_system(
                            f"No packets received for {int(time_since_last_packet)}s. Connection may be stale.",
                            error=True
                        )
                        self.log_system("Triggering reconnection...", error=True)
                        
                        # Manually trigger disconnect to start reconnection process
                        self.is_connected = False
                        self.on_disconnect()
                        continue

                # Request telemetry from the device (also serves as a lightweight keepalive)
                loop = asyncio.get_event_loop()

                def request_telemetry():
                    try:
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
            except Exception as e:
                # Log unexpected errors but continue
                self.log_system(f"Error in stats loop: {e}")

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
    parser = argparse.ArgumentParser(
        description="Meshtastic Terminal - A modern terminal UI for Meshtastic mesh networks"
    )
    parser.add_argument(
        "-a", "--auto-connect",
        action="store_true",
        help="Auto-connect to the first available serial port"
    )
    args = parser.parse_args()
    
    app = ChatMonitor(auto_connect=args.auto_connect)
    app.run()


if __name__ == "__main__":
    main()
