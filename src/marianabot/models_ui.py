"""Model preferences for new research runs, separate from saved run snapshots."""

from pydantic import ValidationError
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from marianabot.config import Config

EFFORTS = [
    ("Extra high" if value == "xhigh" else value.capitalize(), value)
    for value in ("low", "medium", "high", "xhigh", "max")
]


class ModelsScreen(ModalScreen[Config | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: Config, current_run: Config | None = None):
        super().__init__()
        self.config = config.model_copy(deep=True)
        self.current_run = current_run

    def compose(self) -> ComposeResult:
        with Vertical(id="model-dialog"):
            yield Static("Models for new research", classes="dialog-title")
            yield Static(
                "Use full model IDs from your provider. Availability and supported effort depend on the model.",
                id="model-explanation",
            )
            with Horizontal(classes="model-label"):
                yield Static("ChatGPT · research + master (RB / MB)", classes="model-name")
                yield Static("Effort", classes="effort-label")
            with Horizontal(classes="model-row"):
                yield Input(self.config.rb.model, id="rb-model", placeholder="Model ID")
                yield Select(
                    EFFORTS, value=self.config.rb.effort, allow_blank=False, id="rb-effort"
                )
            with Horizontal(classes="model-label"):
                yield Static("Claude · judge (JB)", classes="model-name")
                yield Static("Effort", classes="effort-label")
            with Horizontal(classes="model-row"):
                yield Input(self.config.jb.model, id="jb-model", placeholder="Model ID")
                yield Select(
                    EFFORTS, value=self.config.jb.effort, allow_blank=False, id="jb-effort"
                )
            if self.current_run:
                yield Static(
                    f"This run keeps {self.current_run.rb.model} ({self.current_run.rb.effort}) / {self.current_run.jb.model} ({self.current_run.jb.effort}).",
                    id="current-models",
                )
            yield Static("", id="model-error", markup=False)
            with Horizontal(id="model-actions"):
                yield Button("Save", id="save-models")
                yield Button("Use defaults", id="default-models")
                yield Button("Cancel", id="cancel-models")

    def on_mount(self):
        self.query_one("#rb-model", Input).focus()
        for provider in ("rb", "jb"):
            self.query_one(
                f"#{provider}-effort"
            ).tooltip = "Reasoning effort. Higher levels can consume more allowance; the provider must support the selected level."

    @on(Button.Pressed, "#default-models")
    def defaults(self):
        defaults = Config()
        for provider in ("rb", "jb"):
            brain = getattr(defaults, provider)
            self.query_one(f"#{provider}-model", Input).value = brain.model
            self.query_one(f"#{provider}-effort", Select).value = brain.effort
        self.query_one("#model-error", Static).update(
            "Defaults selected. Save to apply them to new runs."
        )

    @on(Button.Pressed, "#save-models")
    def save(self):
        data = self.config.model_dump()
        for provider in ("rb", "jb"):
            data[provider]["model"] = self.query_one(f"#{provider}-model", Input).value.strip()
            data[provider]["effort"] = self.query_one(f"#{provider}-effort", Select).value
        try:
            config = Config.model_validate(data)
        except ValidationError:
            self.query_one("#model-error", Static).update(
                "Enter a model ID using letters, digits, dots, underscores or hyphens (1–100 characters)."
            )
            return
        self.dismiss(config)

    @on(Button.Pressed, "#cancel-models")
    def action_cancel(self):
        self.dismiss(None)
