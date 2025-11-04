"""Raw network activity monitoring screen for Meshtastic TUI."""

import asyncio
from datetime import datetime, timedelta
from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal
from textual.widgets import Static, Label
from textual.screen import ModalScreen
from textual.reactive import reactive
from pubsub import pub


class NodeGridCell(Static):
    """A single cell in the node grid."""
    
    node_id: reactive[str] = reactive("")
    packet_type: reactive[str] = reactive("")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_activity = None
    
    def watch_node_id(self, node_id: str) -> None:
        """Update cell display when node ID changes."""
        if node_id:
            # Extract just the hex portion (strip the '!' prefix if present)
            if node_id.startswith('!'):
                hex_id = node_id[1:]
            else:
                hex_id = node_id
            self.update(hex_id)
        else:
            self.update("")
    
    def watch_packet_type(self, packet_type: str) -> None:
        """Update cell styling based on packet type."""
        # Remove all packet type classes
        for ptype in ["text", "position", "telemetry", "nodeinfo", "traceroute", "routing", "unknown"]:
            self.remove_class(ptype)
        
        # Add the current packet type class
        if packet_type:
            self.add_class(packet_type)


class RawMonitorScreen(ModalScreen):
    """Modal screen to display raw network activity in a grid format."""
    
    CSS = """
    RawMonitorScreen {
        align: center middle;
    }
    
    #raw-monitor-container {
        width: 110;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    
    #raw-monitor-title {
        width: 100%;
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    
    #raw-monitor-subtitle {
        width: 100%;
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    
    #node-grid {
        width: 100%;
        height: auto;
        grid-size: 10;
        grid-gutter: 1 0;
        margin-top: 1;
    }
    
    NodeGridCell {
        width: 9;
        height: 1;
        content-align: center middle;
        border: none;
        background: $surface;
        color: $text-muted;
    }
    
    /* Color coding by packet type */
    NodeGridCell.text {
        text-style: bold;
        color: $text;
        background: $accent;
    }
    
    NodeGridCell.position {
        text-style: bold;
        color: $text;
        background: $warning;
    }
    
    NodeGridCell.telemetry {
        text-style: bold;
        color: $text;
        background: $success;
    }
    
    NodeGridCell.nodeinfo {
        text-style: bold;
        color: $text;
        background: $primary;
    }
    
    NodeGridCell.traceroute {
        text-style: bold;
        color: $text;
        background: #ff00ff;
    }
    
    NodeGridCell.routing {
        text-style: bold;
        color: $text-muted;
        background: $boost;
    }
    
    NodeGridCell.unknown {
        text-style: bold;
        color: $text;
        background: $error;
    }
    
    #legend-container {
        width: 100%;
        height: auto;
        margin-top: 2;
        padding: 0;
    }
    
    #legend-title {
        width: 100%;
        text-align: center;
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }
    
    Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }
    
    .legend-item {
        width: auto;
        height: auto;
        padding: 0 2;
    }
    
    #raw-monitor-help {
        width: 100%;
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        ("escape", "close_monitor", "Close"),
        ("ctrl+r", "close_monitor", "Close"),
        ("q", "close_monitor", "Close"),
    ]
    
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.node_activity = {}  # {node_id: {"time": datetime, "packet_type": str}}
        self.cells = []
        self.update_worker = None
        self.activity_timeout = timedelta(seconds=60)
    
    def compose(self) -> ComposeResult:
        """Create the raw monitor dialog."""
        with Container(id="raw-monitor-container"):
            yield Label("RAW NETWORK MONITOR", id="raw-monitor-title")
            yield Label("Showing all network activity (top 100 nodes)", id="raw-monitor-subtitle")
            
            with Grid(id="node-grid"):
                # Create 100 cells (10x10 grid)
                for i in range(100):
                    cell = NodeGridCell(id=f"cell-{i}")
                    self.cells.append(cell)
                    yield cell
            
            # Legend
            with Container(id="legend-container"):
                yield Label("Packet Type Legend:", id="legend-title")
                with Horizontal():
                    yield Static("[#00a0e9 bold]TEXT[/]", classes="legend-item")
                    yield Static("[#ffa500 bold]POSITION[/]", classes="legend-item")
                    yield Static("[#00ff00 bold]TELEMETRY[/]", classes="legend-item")
                    yield Static("[#7f7fff bold]NODEINFO[/]", classes="legend-item")
                with Horizontal():
                    yield Static("[#ff00ff bold]TRACEROUTE[/]", classes="legend-item")
                    yield Static("[#808080 bold]ROUTING[/]", classes="legend-item")
                    yield Static("[#ff0000 bold]OTHER[/]", classes="legend-item")
            
            yield Label("Press ESC or Ctrl+R to exit", id="raw-monitor-help")
    
    def on_mount(self) -> None:
        """Subscribe to all Meshtastic packets and start update loop."""
        # Subscribe to ALL packets
        pub.subscribe(self.on_packet_received, "meshtastic.receive")
        
        # Start periodic update loop to clean up stale nodes and refresh display
        self.update_worker = self.run_worker(self.update_grid_loop(), exclusive=False)
    
    def on_packet_received(self, packet, interface=None) -> None:
        """Handle any packet received from the mesh network."""
        # Extract sender node ID
        from_id = packet.get("fromId")
        if not from_id:
            # Try to get from numeric ID
            from_num = packet.get("from")
            if from_num and hasattr(self.iface, "nodesByNum"):
                node_info = self.iface.nodesByNum.get(from_num, {})
                from_id = node_info.get("user", {}).get("id")
                if not from_id:
                    from_id = f"!{from_num:08x}"
        
        # Skip if we couldn't get a node ID
        if not from_id:
            return
        
        # Determine packet type
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum", "unknown")
        
        # Map portnum to display type
        packet_type_map = {
            "TEXT_MESSAGE_APP": "text",
            1: "text",
            "POSITION_APP": "position",
            "TELEMETRY_APP": "telemetry",
            "NODEINFO_APP": "nodeinfo",
            "TRACEROUTE_APP": "traceroute",
            "ROUTING_APP": "routing",
        }
        
        packet_type = packet_type_map.get(portnum, "unknown")
        
        # Update activity timestamp and packet type for this node
        self.node_activity[from_id] = {
            "time": datetime.now(),
            "packet_type": packet_type
        }
        
        # Trigger grid update
        self.call_after_refresh(self.update_grid_display)
    
    def update_grid_display(self) -> None:
        """Update the grid display with current node activity."""
        now = datetime.now()
        
        # Remove nodes that haven't been active in the timeout period
        stale_nodes = [
            node_id for node_id, activity_data in self.node_activity.items()
            if now - activity_data["time"] > self.activity_timeout
        ]
        for node_id in stale_nodes:
            del self.node_activity[node_id]
        
        # Sort nodes alphabetically and take top 100
        sorted_nodes = sorted(self.node_activity.keys())[:100]
        
        # Update cells
        for i, cell in enumerate(self.cells):
            if i < len(sorted_nodes):
                node_id = sorted_nodes[i]
                activity_data = self.node_activity[node_id]
                
                cell.node_id = node_id
                
                # Set packet type to color the cell
                time_since_activity = now - activity_data["time"]
                if time_since_activity.total_seconds() < 3.0:
                    # Show color for recent activity
                    cell.packet_type = activity_data["packet_type"]
                else:
                    # Fade to inactive after 3 seconds
                    cell.packet_type = ""
            else:
                # Empty cell
                cell.node_id = ""
                cell.packet_type = ""
        
        # Update subtitle with node count
        subtitle = self.query_one("#raw-monitor-subtitle", Label)
        subtitle.update(f"Showing {len(sorted_nodes)} active nodes (last 60s)")
    
    async def update_grid_loop(self) -> None:
        """Periodically update the grid to remove stale nodes and fade active indicators."""
        while True:
            try:
                await asyncio.sleep(1)  # Update every second
                self.update_grid_display()
            except asyncio.CancelledError:
                break
            except Exception:
                pass  # Ignore errors in update loop
    
    def action_close_monitor(self) -> None:
        """Close the raw monitor screen."""
        self.dismiss()
    
    async def on_unmount(self) -> None:
        """Clean up when screen is closed."""
        # Unsubscribe from packets
        try:
            pub.unsubscribe(self.on_packet_received, "meshtastic.receive")
        except Exception:
            pass
        
        # Cancel update worker
        if self.update_worker:
            self.update_worker.cancel()
