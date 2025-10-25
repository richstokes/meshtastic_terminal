"""User selector modal screen for direct messaging in Meshtastic TUI."""

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Button, Label, ListItem, ListView
from textual.screen import ModalScreen
from textual import on, events


class UserSelectorScreen(ModalScreen):
    """Modal screen for selecting a user to direct message."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Cancel"),
        ("enter", "select_user", "Select"),
        ("up", "focus_previous", "Up"),
        ("down", "focus_next", "Down"),
    ]

    def __init__(self, known_nodes: dict, my_node_id: str = None):
        """
        Initialize the user selector.
        
        Args:
            known_nodes: Dictionary of known nodes {node_id: {name, last_seen, first_seen}}
            my_node_id: The current user's node ID (to exclude from list)
        """
        super().__init__()
        self.known_nodes = known_nodes
        self.my_node_id = my_node_id
        self.user_list = []  # List of actual node IDs
        self.id_map = {}  # Map sanitized IDs to actual node IDs

    @staticmethod
    def sanitize_id(node_id: str) -> str:
        """Convert node ID to valid Textual widget ID."""
        # Replace ! with node_ prefix and remove any other invalid chars
        return "node_" + node_id.lstrip("!").replace("-", "_")

    def compose(self) -> ComposeResult:
        """Create the user selector dialog."""
        with Container(id="user-selector-dialog"):
            yield Label("Select user to message:", id="user-selector-title")
            
            # Build list of users (excluding self)
            for node_id, node_info in sorted(
                self.known_nodes.items(),
                key=lambda x: x[1].get("name", x[0]).lower()
            ):
                # Skip our own node
                if node_id == self.my_node_id:
                    continue
                    
                self.user_list.append(node_id)
                
            # If no users available, show message
            if not self.user_list:
                yield Label("No other users available", id="no-users-message")
            else:
                # Create scrollable list view
                list_view = ListView(id="user-list")
                list_view.can_focus = True
                yield list_view

    def on_mount(self) -> None:
        """Populate the list and focus it when mounted."""
        if self.user_list:
            list_view = self.query_one("#user-list", ListView)
            # Add all list items
            for node_id in self.user_list:
                node_info = self.known_nodes[node_id]
                display_name = node_info.get("name", node_id)
                
                if display_name != node_id:
                    label_text = f"{display_name} ({node_id})"
                else:
                    label_text = node_id
                
                # Sanitize the node_id for use as widget ID
                sanitized_id = self.sanitize_id(node_id)
                self.id_map[sanitized_id] = node_id
                    
                list_view.append(ListItem(Label(label_text), id=sanitized_id))
            
            # Focus the list view
            self.set_focus(list_view)

    @on(ListView.Selected)
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle user selection from ListView."""
        if event.item and event.item.id:
            sanitized_id = event.item.id
            # Map back to actual node ID
            actual_node_id = self.id_map.get(sanitized_id)
            self.dismiss(actual_node_id)

    def action_select_user(self) -> None:
        """Select the currently highlighted user (for Enter key binding)."""
        if not self.user_list:
            self.dismiss(None)
            return
            
        list_view = self.query_one("#user-list", ListView)
        if list_view.highlighted_child:
            sanitized_id = list_view.highlighted_child.id
            # Map back to actual node ID
            actual_node_id = self.id_map.get(sanitized_id)
            self.dismiss(actual_node_id)

    def action_dismiss_dialog(self) -> None:
        """Dismiss dialog without selecting."""
        self.dismiss(None)

    def action_focus_previous(self) -> None:
        """Move selection up in the list."""
        if self.user_list:
            list_view = self.query_one("#user-list", ListView)
            list_view.action_cursor_up()

    def action_focus_next(self) -> None:
        """Move selection down in the list."""
        if self.user_list:
            list_view = self.query_one("#user-list", ListView)
            list_view.action_cursor_down()

    def on_key(self, event: events.Key) -> None:
        """Handle letter key presses to jump to matching users."""
        # Only handle single letter keys
        if len(event.key) == 1 and event.key.isalpha():
            letter = event.key.lower()
            list_view = self.query_one("#user-list", ListView)
            
            # Find first user whose name starts with this letter
            for i, node_id in enumerate(self.user_list):
                node_info = self.known_nodes[node_id]
                display_name = node_info.get("name", node_id)
                
                # Check if name starts with the letter
                if display_name.lower().startswith(letter):
                    # Move to this item
                    list_view.index = i
                    event.prevent_default()
                    event.stop()
                    break
