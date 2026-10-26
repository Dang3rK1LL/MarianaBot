"""Choose where a new research run should keep its files."""

from dataclasses import dataclass
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from marianabot.workspaces import default_work_folder, resolve_work_folder


@dataclass
class ResearchOptions:
    folder: Path
    mode: str
    questions: int


class WorkFolderScreen(ModalScreen[ResearchOptions | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="work-dialog"):
            yield Static("Work folder for this research", classes="dialog-title")
            with VerticalScroll(id="research-options-fields"):
                yield Static(
                    "A new research subfolder will hold the brief, workspace and results.",
                    id="work-explanation",
                )
                yield Input(str(default_work_folder()), id="work-folder", placeholder="Folder path")
                yield Static("Research mode", classes="model-label")
                yield Select(
                    [
                        ("Automatic — research runs independently", "automatic"),
                        ("Guided — quick questions before each round", "guided"),
                    ],
                    value="automatic",
                    allow_blank=False,
                    id="research-mode",
                )
                yield Static("Questions per round", id="question-count-label")
                yield Select(
                    [(str(n), n) for n in range(1, 11)],
                    value=3,
                    allow_blank=False,
                    id="question-count",
                )
            yield Static("", id="work-error", markup=False)
            with Horizontal(id="work-actions"):
                yield Button("Start research", id="choose-work-folder")
                yield Button("Cancel", id="cancel-work-folder")

    def on_mount(self):
        self.query_one("#work-folder", Input).focus()
        self.mode_changed()

    @on(Select.Changed, "#research-mode")
    def mode_changed(self):
        guided = self.query_one("#research-mode", Select).value == "guided"
        self.query_one("#question-count-label").display = guided
        self.query_one("#question-count").display = guided

    @on(Input.Submitted)
    @on(Button.Pressed, "#choose-work-folder")
    def choose(self):
        try:
            folder = resolve_work_folder(self.query_one("#work-folder", Input).value)
        except (ValueError, OSError) as exc:
            self.query_one("#work-error", Static).update(str(exc))
            return
        self.dismiss(
            ResearchOptions(
                folder,
                str(self.query_one("#research-mode", Select).value),
                int(self.query_one("#question-count", Select).value),
            )
        )

    @on(Button.Pressed, "#cancel-work-folder")
    def action_cancel(self):
        self.dismiss(None)
