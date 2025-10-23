"""User name setter modal screen for Meshtastic TUI."""

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Button, Label, Input
from textual.screen import ModalScreen
from textual import on


class UserNameSetterScreen(ModalScreen):
    """Modal screen for setting user long name and short name."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Cancel"),
    ]

    def __init__(self, current_long_name: str = "", current_short_name: str = ""):
        super().__init__()
        self.current_long_name = current_long_name
        self.current_short_name = current_short_name

    def compose(self) -> ComposeResult:
        """Create the user name setter dialog."""
        with Container(id="username-dialog"):
            yield Label("Set User Names", id="username-title")

            with Vertical(id="username-form"):
                yield Label("Long Name (max 36 bytes):", classes="input-label")
                yield Input(
                    placeholder="Your long name",
                    value=self.current_long_name,
                    max_length=36,
                    id="long-name-input",
                )

                yield Label("Short Name (max 4 chars):", classes="input-label")
                yield Input(
                    placeholder="SHRT",
                    value=self.current_short_name,
                    max_length=4,
                    id="short-name-input",
                )

                with Container(id="username-buttons"):
                    yield Button("Save", variant="primary", id="save-button")
                    yield Button("Cancel", variant="default", id="cancel-button")

    def on_mount(self) -> None:
        """Focus long name input when mounted."""
        long_name_input = self.query_one("#long-name-input", Input)
        long_name_input.focus()

    @on(Button.Pressed, "#save-button")
    def handle_save(self) -> None:
        """Save the user names."""
        long_name_input = self.query_one("#long-name-input", Input)
        short_name_input = self.query_one("#short-name-input", Input)

        long_name = long_name_input.value.strip()
        short_name = short_name_input.value.strip()

        # Return a tuple of (long_name, short_name)
        self.dismiss((long_name, short_name))

    @on(Button.Pressed, "#cancel-button")
    def handle_cancel(self) -> None:
        """Cancel without saving."""
        self.dismiss(None)

    def action_dismiss_dialog(self) -> None:
        """Dismiss dialog without saving."""
        self.dismiss(None)
