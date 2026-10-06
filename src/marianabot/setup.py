"""Local onboarding. No provider requests or remote connections."""

from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from marianabot.config import EFFORTS, BrainConfig, Config, load_config, save_model_preferences
from marianabot.store import Store


def choose_brain(label: str, current: BrainConfig) -> BrainConfig:
    while True:
        model = typer.prompt(f"{label} model", default=current.model).strip()
        effort = (
            typer.prompt(f"{label} effort ({', '.join(EFFORTS)})", default=current.effort)
            .strip()
            .lower()
        )
        try:
            return BrainConfig.model_validate(
                {**current.model_dump(), "model": model, "effort": effort}
            )
        except ValueError:
            typer.echo(
                "Use a model ID with letters, digits, dots, hyphens or underscores and a listed effort."
            )


def configure(path: Path, directory: Path, console: Console):
    source = path.read_text(encoding="utf-8") if path.exists() else None
    config = load_config(path) if source is not None else Config()
    console.print("[bold]MarianaBot setup[/bold]")
    console.print("Research runs on this machine. Your data and settings stay here.")
    console.print("Press Enter to keep each choice. Model access depends on your subscriptions.")
    config.rb = choose_brain("Research + master", config.rb)
    config.jb = choose_brain("Judge", config.jb)
    console.print(
        "\nBefore live research, disable paid extra usage, usage credits and automatic credit "
        "purchases in both provider accounts. MarianaBot cannot verify those settings."
    )
    overage = typer.confirm("Have you checked that all of these are disabled?", default=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_model_preferences(path, config, source, overage_disabled=overage)
    store = Store(directory)
    store.close()
    console.print(Text(f"\nSaved {path.resolve()}"))
    console.print(Text(f"Research data: {directory.resolve()}"))
    console.print("Existing research keeps its saved model choices.")
    if not overage:
        console.print("Live research is locked. You can still use mariana chat --demo.")
    console.print(
        "\nSign in with codex login and claude auth login --claudeai, then run mariana doctor.\n"
        "Open chat with mariana. You can change models later with /models."
    )
