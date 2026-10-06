"""Choose where a new research run should keep its files."""

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from marianabot.workspaces import default_work_folder, resolve_work_folder


class WorkFolderScreen(ModalScreen[Path | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="work-dialog"):
            yield Static("Work folder for this research", classes="dialog-title")
            yield Static(
                "A new research subfolder will hold the brief, client workspace and results.",
                id="work-explanation",
            )
            yield Input(str(default_work_folder()), id="work-folder", placeholder="Folder path")
            yield Static("", id="work-error", markup=False)
            with Horizontal(id="work-actions"):
                yield Button("Start research", id="choose-work-folder")
                yield Button("Cancel", id="cancel-work-folder")

    def on_mount(self):
        self.query_one(Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#choose-work-folder")
    def choose(self):
        try:
            folder = resolve_work_folder(self.query_one(Input).value)
        except (ValueError, OSError) as exc:
            self.query_one("#work-error", Static).update(str(exc))
            return
        self.dismiss(folder)

    @on(Button.Pressed, "#cancel-work-folder")
    def action_cancel(self):
        self.dismiss(None)
