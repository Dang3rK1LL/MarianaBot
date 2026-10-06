"""Choose provider-reported models and effort levels for new research runs."""

import asyncio
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static

from marianabot.clients import ClientError
from marianabot.config import Config
from marianabot.model_catalog import ModelOption, load_models


def effort_label(value: str) -> str:
    return {"xhigh": "Extra high", "auto": "Default"}.get(value, value.capitalize())


class ModelsScreen(ModalScreen[Config | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self, config: Config, current_run: Config | None = None, *, cwd: Path | None = None
    ):
        super().__init__()
        self.config = config.model_copy(deep=True)
        self.current_run = current_run
        self.cwd = cwd or Path.cwd()
        self.catalogs: dict[str, dict[str, ModelOption]] = {}
        self.errors: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="model-dialog"):
            yield Static("Models for new research", classes="dialog-title")
            yield Static("Choose a model and its reasoning effort.", id="model-explanation")
            for provider, title in (
                ("rb", "ChatGPT · research + master (RB / MB)"),
                ("jb", "Claude · judge (JB)"),
            ):
                brain = getattr(self.config, provider)
                with Horizontal(classes="model-label"):
                    yield Static(title, classes="model-name")
                    yield Static("Effort", classes="effort-label")
                with Horizontal(classes="model-row"):
                    yield Select(
                        [(brain.model, brain.model)],
                        value=brain.model,
                        allow_blank=False,
                        id=f"{provider}-model",
                        classes="model-choice",
                        disabled=True,
                    )
                    yield Select(
                        [(effort_label(brain.effort), brain.effort)],
                        value=brain.effort,
                        allow_blank=False,
                        id=f"{provider}-effort",
                        classes="effort-choice",
                        disabled=True,
                    )
            yield Static("Loading available models…", id="model-status", markup=False)
            if self.current_run:
                yield Static(
                    f"This run keeps {self.current_run.rb.model} ({effort_label(self.current_run.rb.effort)}) / {self.current_run.jb.model} ({effort_label(self.current_run.jb.effort)}).",
                    id="current-models",
                )
            yield Static("", id="model-error", markup=False)
            with Horizontal(id="model-actions"):
                yield Button("Save", id="save-models", disabled=True)
                yield Button("Use defaults", id="default-models", disabled=True)
                yield Button("Retry", id="retry-models", disabled=True)
                yield Button("Cancel", id="cancel-models")

    def on_mount(self):
        for provider in ("rb", "jb"):
            self.query_one(
                f"#{provider}-effort"
            ).tooltip = "Reasoning effort. Higher levels can use more of your allowance."
        self.run_worker(self.load_catalogs(), exclusive=True)

    async def load_catalogs(self):
        self.catalogs.clear()
        self.errors.clear()
        for button in ("save", "default", "retry"):
            self.query_one(f"#{button}-models", Button).disabled = True
        self.query_one("#model-status", Static).update("Loading available models…")
        for provider in ("rb", "jb"):
            for field in ("model", "effort"):
                self.query_one(f"#{provider}-{field}", Select).disabled = True

        async def load(provider):
            try:
                models = await load_models(provider, self.config, self.cwd)
                self.catalogs[provider] = {model.model: model for model in models}
                selector = self.query_one(f"#{provider}-model", Select)
                selected = selector.value
                options = [(model.label, model.model) for model in models]
                if selected not in self.catalogs[provider]:
                    options.append((f"{selected} (unavailable)", selected))
                with self.prevent(Select.Changed):
                    selector.set_options(options)
                    selector.value = selected
                    selector.disabled = False
                    self.sync_effort(provider)
            except (ClientError, OSError, ValueError):
                self.errors[provider] = "Model list unavailable. Check login, then Retry."

        await asyncio.gather(load("rb"), load("jb"))
        ready = len(self.catalogs) == 2 and not self.errors
        self.query_one("#save-models", Button).disabled = not ready
        self.query_one("#default-models", Button).disabled = not ready
        self.query_one("#retry-models", Button).disabled = False
        status = "\n".join(
            f"{'ChatGPT' if provider == 'rb' else 'Claude'}: {error}"
            for provider, error in self.errors.items()
        )
        self.query_one("#model-status", Static).update(status or "Model lists are up to date.")
        if ready:
            self.query_one("#rb-model", Select).focus()

    def sync_effort(self, provider: str, preferred: str | None = None):
        selector = self.query_one(f"#{provider}-effort", Select)
        model = self.catalogs.get(provider, {}).get(
            self.query_one(f"#{provider}-model", Select).value
        )
        if not model:
            selector.disabled = True
            return
        levels = model.efforts or ("auto",)
        selected = preferred or selector.value
        if selected not in levels:
            selected = model.default_effort
        with self.prevent(Select.Changed):
            selector.set_options([(effort_label(value), value) for value in levels])
            selector.value = selected
        selector.disabled = not model.efforts

    @on(Select.Changed, "#rb-model")
    @on(Select.Changed, "#jb-model")
    def model_changed(self, event: Select.Changed):
        if event.value == event.select.value:
            self.sync_effort(event.select.id.split("-")[0])
            self.query_one("#model-error", Static).update("")

    @on(Button.Pressed, "#retry-models")
    def retry(self):
        self.run_worker(self.load_catalogs(), exclusive=True)

    @on(Button.Pressed, "#default-models")
    def defaults(self):
        defaults = Config()
        if any(
            getattr(defaults, provider).model not in self.catalogs.get(provider, {})
            for provider in ("rb", "jb")
        ):
            self.query_one("#model-error", Static).update(
                "A default model is unavailable. Choose from the available models."
            )
            return
        with self.prevent(Select.Changed):
            for provider in ("rb", "jb"):
                brain = getattr(defaults, provider)
                self.query_one(f"#{provider}-model", Select).value = brain.model
                self.sync_effort(provider, brain.effort)
        self.query_one("#model-error", Static).update(
            "Defaults selected. Save to apply them to new runs."
        )

    @on(Button.Pressed, "#save-models")
    def save(self):
        if len(self.catalogs) != 2 or self.errors:
            return
        data = self.config.model_dump()
        for provider in ("rb", "jb"):
            selected = self.query_one(f"#{provider}-model", Select).value
            model = self.catalogs[provider].get(selected)
            effort = self.query_one(f"#{provider}-effort", Select).value
            if not model or effort not in (model.efforts or ("auto",)):
                self.query_one("#model-error", Static).update(
                    f"Choose an available {'ChatGPT' if provider == 'rb' else 'Claude'} model and supported effort."
                )
                return
            data[provider].update(model=selected, effort=effort)
        self.dismiss(Config.model_validate(data))

    @on(Button.Pressed, "#cancel-models")
    def action_cancel(self):
        self.dismiss(None)
