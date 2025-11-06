"""Node list modal screen for viewing mesh network nodes in Meshtastic TUI."""

from datetime import datetime
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import DataTable, Label, Static
from textual.screen import ModalScreen
from textual import on, events
from .node_detail import NodeDetailScreen


class NodeListScreen(ModalScreen):
    """Modal screen for displaying all known nodes with detailed information."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Close"),
        ("q", "dismiss_dialog", "Close"),
        ("enter", "view_node_detail", "View Details"),
    ]

    def __init__(self, interface, my_node_id: str = None):
        """
        Initialize the node list screen.
        
        Args:
            interface: The Meshtastic interface object with nodes database
            my_node_id: The current user's node ID
        """
        super().__init__()
        self.interface = interface
        self.my_node_id = my_node_id
        self.node_rows = []  # List of (name, node_id, node_data) tuples for navigation
        self.nodes_dict = {}  # Map node_id to full node_data

    def compose(self) -> ComposeResult:
        """Create the node list dialog."""
        with Container(id="node-list-dialog"):
            yield Label("Mesh Network Nodes", id="node-list-title")
            
            # Create scrollable data table
            table = DataTable(id="node-table", zebra_stripes=True)
            table.cursor_type = "row"
            yield table
            
            yield Static("Press ENTER for details | ESC to close | Letter keys to jump", id="node-list-footer")

    def on_mount(self) -> None:
        """Populate the node list when mounted."""
        table = self.query_one("#node-table", DataTable)
        
        # Add columns
        table.add_columns(
            "Node Name",
            "ID",
            "Model",
            "Role",
            "SNR",
            "Last Heard",
        )
        
        # Get nodes from interface and store in dictionary
        nodes = []
        if hasattr(self.interface, "nodes") and self.interface.nodes:
            for node_id, node_data in self.interface.nodes.items():
                nodes.append((node_id, node_data))
                self.nodes_dict[node_id] = node_data
        
        # Sort nodes: our node first, then by name
        def sort_key(item):
            node_id, node_data = item
            if node_id == self.my_node_id:
                return (0, "")  # Our node comes first
            user = node_data.get("user", {})
            name = user.get("longName") or user.get("shortName") or node_id
            return (1, name.lower())
        
        nodes.sort(key=sort_key)
        
        # Populate table and track node names for jump functionality
        for node_id, node_data in nodes:
            # Extract node information
            user = node_data.get("user", {})
            device_metrics = node_data.get("deviceMetrics", {})
            position = node_data.get("position", {})
            
            # Name
            long_name = user.get("longName", "")
            short_name = user.get("shortName", "")
            if long_name and short_name and long_name != short_name:
                name = f"{long_name} ({short_name})"
            elif long_name:
                name = long_name
            elif short_name:
                name = short_name
            else:
                name = "Unknown"
            
            # Add indicator if it's our node
            if node_id == self.my_node_id:
                name = f"★ {name}"
            
            # Hardware model
            hw_model = user.get("hwModel", "")
            if hw_model:
                # Map numeric codes to readable names if possible
                hw_model_names = {
                    0: "UNSET",
                    1: "TLORA_V2",
                    2: "TLORA_V1",
                    3: "TLORA_V2_1_1P6",
                    4: "TBEAM",
                    5: "HELTEC_V2_0",
                    6: "TBEAM_V0P7",
                    7: "T_ECHO",
                    8: "TLORA_V1_1P3",
                    9: "RAK4631",
                    10: "HELTEC_V2_1",
                    11: "HELTEC_V1",
                    12: "LILYGO_TBEAM_S3_CORE",
                    13: "RAK11200",
                    14: "NANO_G1",
                    15: "TLORA_V2_1_1P8",
                    16: "TLORA_T3_S3",
                    17: "NANO_G1_EXPLORER",
                    18: "NANO_G2_ULTRA",
                    25: "STATION_G1",
                    26: "RAK11310",
                    29: "SENSELORA_RP2040",
                    31: "SENSELORA_S3",
                    32: "CANARYONE",
                    33: "RP2040_LORA",
                    39: "HELTEC_V3",
                    41: "HELTEC_WSL_V3",
                    42: "BETAFPV_2400_TX",
                    43: "BETAFPV_900_NANO_TX",
                    47: "RPI_PICO",
                    48: "HELTEC_WIRELESS_TRACKER",
                    49: "HELTEC_WIRELESS_PAPER",
                    50: "T_DECK",
                    51: "T_WATCH_S3",
                    52: "PICOMPUTER_S3",
                    53: "HELTEC_HT62",
                    61: "UNPHONE",
                    64: "TD_LORAC",
                    65: "CDEBYTE_EORA_S3",
                    66: "TWC_MESH_V4",
                    67: "NRF52_UNKNOWN",
                    68: "PORTDUINO",
                    69: "ANDROID_SIM",
                    70: "DIY_V1",
                    71: "NRF52840_PCA10059",
                    72: "DR_DEV",
                    73: "M5STACK",
                    74: "HELTEC_V2",
                    75: "HELTEC_V1",
                    76: "LILYGO_TBEAM_V1_1",
                    77: "TBEAM_V1",
                    78: "LILYGO_TBEAM_S3_CORE",
                    200: "WIO_WM1110",
                    201: "RAK2560",
                    254: "PRIVATE_HW",
                    255: "UNSET",
                }
                if isinstance(hw_model, int):
                    hw_model = hw_model_names.get(hw_model, f"HW_{hw_model}")
                elif hasattr(hw_model, "name"):
                    hw_model = hw_model.name
                # Shorten long model names for display
                if len(str(hw_model)) > 15:
                    hw_model = str(hw_model)[:12] + "..."
            else:
                hw_model = "N/A"
            
            # Role
            role = user.get("role", "")
            if role is not None and role != "":
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
                if isinstance(role, int):
                    role = role_names.get(role, f"ROLE_{role}")
                elif hasattr(role, "name"):
                    role = role.name
            else:
                role = "N/A"
            
            # SNR (Signal-to-Noise Ratio)
            snr = node_data.get("snr")
            if snr is not None:
                snr_str = f"{snr:.1f} dB"
            else:
                snr_str = "N/A"
            
            # Last heard
            last_heard = node_data.get("lastHeard")
            if last_heard:
                try:
                    # lastHeard is typically a Unix timestamp
                    dt = datetime.fromtimestamp(last_heard)
                    now = datetime.now()
                    delta = now - dt
                    
                    # Format relative time
                    if delta.total_seconds() < 60:
                        time_str = "just now"
                    elif delta.total_seconds() < 3600:
                        mins = int(delta.total_seconds() / 60)
                        time_str = f"{mins}m ago"
                    elif delta.total_seconds() < 86400:
                        hours = int(delta.total_seconds() / 3600)
                        time_str = f"{hours}h ago"
                    else:
                        days = int(delta.total_seconds() / 86400)
                        time_str = f"{days}d ago"
                except Exception:
                    time_str = "Unknown"
            else:
                time_str = "Never"
            
            # Store node info for navigation (without star indicator)
            clean_name = long_name or short_name or "Unknown"
            self.node_rows.append((clean_name, node_id, node_data))
            
            # Add row to table
            table.add_row(
                name,
                node_id,
                str(hw_model),
                str(role),
                snr_str,
                time_str,
            )
        
        # If no nodes found
        if table.row_count == 0:
            table.add_row("No nodes found", "", "", "", "", "")

    def action_dismiss_dialog(self) -> None:
        """Dismiss the dialog."""
        self.dismiss()

    def action_view_node_detail(self) -> None:
        """Open detail view for the currently selected node."""
        self._show_node_detail()

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection (Enter key or click)."""
        self._show_node_detail()

    def _show_node_detail(self) -> None:
        """Show the detail screen for the currently selected node."""
        table = self.query_one("#node-table", DataTable)
        
        # Get the current cursor row
        cursor_row = table.cursor_row
        if cursor_row < 0 or cursor_row >= len(self.node_rows):
            return
        
        # Get node data for the selected row
        _, node_id, node_data = self.node_rows[cursor_row]
        is_my_node = (node_id == self.my_node_id)
        
        # Push the detail screen as a nested modal
        self.app.push_screen(NodeDetailScreen(node_id, node_data, is_my_node))

    def on_key(self, event: events.Key) -> None:
        """Handle letter key presses to jump to matching nodes alphabetically."""
        # Only handle single letter keys, but not 'q' (reserved for quit binding)
        if len(event.key) == 1 and event.key.isalpha() and event.key.lower() != 'q':
            letter = event.key.lower()
            table = self.query_one("#node-table", DataTable)
            
            # Skip our own node at index 0 when searching
            # Find first node whose name starts with this letter
            for i, (node_name, node_id, _) in enumerate(self.node_rows):
                # Skip our own node (it's always first)
                if i == 0 and node_id == self.my_node_id:
                    continue
                    
                # Check if name starts with the letter
                if node_name.lower().startswith(letter):
                    # Move cursor to this row
                    table.move_cursor(row=i)
                    event.prevent_default()
                    event.stop()
                    break
