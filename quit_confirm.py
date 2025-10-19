"""Quit confirmation modal screen for Meshtastic TUI."""

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button, Label
from textual.screen import ModalScreen


class QuitConfirmScreen(ModalScreen):
    """Modal screen to confirm quitting the application."""

    BINDINGS = [
        ("y", "confirm_quit", "Yes"),
        ("n", "cancel_quit", "No"),
        ("escape", "cancel_quit", "Cancel"),
        ("enter", "select_button", "Select"),
        ("up", "focus_previous", "Up"),
        ("down", "focus_next", "Down"),
        ("left", "focus_previous", "Left"),
        ("right", "focus_next", "Right"),
    ]

    def __init__(self):
        super().__init__()
        self.button_list = []

    def compose(self) -> ComposeResult:
        """Create the quit confirmation dialog."""
        with Container(id="quit-dialog"):
            yield Label("Are you sure you want to quit?", id="quit-message")
            with Container(id="quit-buttons"):
                yield Button("Yes (Y)", id="yes-button", variant="error")
                yield Button("No (N)", id="no-button", variant="primary")

    def on_mount(self) -> None:
        """Focus the No button by default (safer choice)."""
        # Store button list for navigation
        self.button_list = list(self.query(Button))
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

    def action_select_button(self) -> None:
        """Select the currently focused button."""
        focused = self.focused
        if isinstance(focused, Button):
            if focused.id == "yes-button":
                self.dismiss(True)
            else:
                self.dismiss(False)

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
