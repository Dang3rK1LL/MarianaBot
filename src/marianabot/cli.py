import asyncio
import signal
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from filelock import FileLock, Timeout
from pydantic import ValidationError
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from marianabot.clients import ClientError, CodexAccount, capture, claude_account, executable
from marianabot.config import DEFAULT_TOML, Config, load_config
from marianabot.engine import Engine
from marianabot.reports import export_run
from marianabot.store import Store
from marianabot.ui import dashboard

app = typer.Typer(
    help="MarianaBot · Take a problem below the surface.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
# Windows redirected terminals can default to cp1250; render Unicode safely.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
console = Console()
DataDir = Annotated[
    Path,
    typer.Option("--data-dir", help="Shared state directory; use the same path in every terminal."),
]
DEFAULT_DATA = Path(".mariana")


def config_at(path: Path, demo: bool = False) -> Config:
    if path.exists():
        return load_config(path)
    if demo:
        return Config()
    raise ValueError("Configuration missing. Run mariana init first.")


def fail(exc):
    if isinstance(exc, ValidationError):
        message = "; ".join(
            ".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False)
        )
    else:
        message = str(exc)
    console.print(Text(message, style="red"))
    raise typer.Exit(2)


@app.command()
def init(config: Path = Path("mariana.toml"), data_dir: DataDir = DEFAULT_DATA):
    """Create local configuration without overwriting an existing file."""
    try:
        if config.exists():
            raise ValueError(f"Configuration already exists: {config}")
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(DEFAULT_TOML, encoding="utf-8")
        store = Store(data_dir)
        store.close()
        console.print(
            Text(f"Created {config}. Subscription mode; no API keys are used.", style="cyan")
        )
        console.print(
            "Try mariana demo, then mariana doctor. See docs/subscriptions.md before live use."
        )
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def new(
    problem: Annotated[str | None, typer.Argument()] = None,
    problem_file: Annotated[Path | None, typer.Option("--problem-file")] = None,
    config: Path = Path("mariana.toml"),
    data_dir: DataDir = DEFAULT_DATA,
):
    """Save a problem. MB prepares the brief when you run it."""
    try:
        if problem and problem_file:
            raise ValueError("Use a problem argument or --problem-file")
        text = problem_file.read_text(encoding="utf-8") if problem_file else problem
        if not text:
            text = typer.prompt("What business problem should MarianaBot investigate?")
        settings = config_at(config)
        store = Store(data_dir)
        try:
            run_id = store.create_run(text, settings)
        finally:
            store.close()
        console.print(Text(run_id, style="bold cyan"))
        console.print(f"Start with: mariana run {run_id} --data-dir {data_dir}")
    except (ValueError, OSError) as exc:
        fail(exc)


async def execute(store: Store, run_id: str, plain: bool):
    engine = Engine(
        store, run_id, log=lambda message: console.print(Text(message)) if plain else None
    )
    original = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        original[sig] = signal.getsignal(sig)
        signal.signal(sig, lambda *_: engine.shutdown.set())
    try:
        if plain:
            await engine.run()
        else:
            task = asyncio.create_task(engine.run())
            try:
                with Live(dashboard(store, run_id), console=console, refresh_per_second=2) as live:
                    while not task.done():
                        live.update(dashboard(store, run_id))
                        await asyncio.sleep(0.3)
                    await task
                    live.update(dashboard(store, run_id))
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    finally:
        for sig, handler in original.items():
            signal.signal(sig, handler)


def run_worker(
    run_id: str, data_dir: Path, plain: bool, resume_run: bool = False, config: Path | None = None
):
    store = Store(data_dir)
    try:
        with FileLock(str(store.directory / "worker.lock"), timeout=0):
            record = store.run(run_id)
            if record["status"] in ("complete", "stopped"):
                console.print(f"Run is {record['status']}. Export it or create a new run.")
                return
            if config:
                settings = load_config(config)
                with store.db:
                    store.db.execute(
                        "UPDATE runs SET config=? WHERE id=?", (settings.model_dump_json(), run_id)
                    )
            if resume_run:
                store.update_run(run_id, control="")
            asyncio.run(execute(store, run_id, plain))
            report = export_run(store, run_id, store.directory / "exports" / run_id)
            console.print(Text(f"Report: {report}", style="cyan"))
            if store.run(run_id)["status"] == "paused":
                raise typer.Exit(3)
    except Timeout:
        fail(
            ValueError("A worker already owns this data directory. Use ask, steer, pause or watch.")
        )
    except (ValueError, OSError, ClientError) as exc:
        fail(exc)
    finally:
        store.close()


@app.command()
def run(run_id: str, data_dir: DataDir = DEFAULT_DATA, plain: bool = False):
    """Run a saved problem; Ctrl+C checkpoints and pauses."""
    run_worker(run_id, data_dir, plain)


@app.command()
def resume(
    run_id: str,
    data_dir: DataDir = DEFAULT_DATA,
    plain: bool = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Resume checkpoints. Optionally apply a validated configuration."""
    run_worker(run_id, data_dir, plain, True, config)


@app.command()
def demo(data_dir: DataDir = Path(".mariana/demo"), plain: bool = False):
    """Run a complete offline demonstration, without logins or network calls."""
    settings = Config()
    settings.research.max_rounds = 2
    settings.research.min_rounds = 1
    settings.rb.concurrency = settings.jb.concurrency = 2
    store = Store(data_dir)
    run_id = store.create_run(
        "Test a small subscription service for independent local businesses.", settings, demo=True
    )
    store.close()
    run_worker(run_id, data_dir, plain)


def queue(run_id: str, kind: str, message: str, data_dir: Path):
    store = Store(data_dir)
    try:
        command_id = store.enqueue(run_id, kind, message)
        timing = "next round boundary" if kind == "steer" else "next available MB slot"
        console.print(f"Queued {kind} #{command_id}; handled at the {timing}.")
    except ValueError as exc:
        fail(exc)
    finally:
        store.close()


@app.command()
def ask(run_id: str, message: str, data_dir: DataDir = DEFAULT_DATA):
    """Ask MB about an active run. Answers appear in messages and exports."""
    queue(run_id, "ask", message, data_dir)


@app.command()
def steer(run_id: str, message: str, data_dir: DataDir = DEFAULT_DATA):
    """Change the brief at the next round boundary; preserve completed work."""
    queue(run_id, "steer", message, data_dir)


def control(run_id: str, action: str, data_dir: Path):
    store = Store(data_dir)
    try:
        record = store.run(run_id)
        if record["status"] in ("complete", "stopped"):
            raise ValueError("This run is already closed")
        store.update_run(run_id, control=action, status="stopped" if action == "stop" else "paused")
        store.event(run_id, f"Owner requested {action}")
        console.print(
            f"{action.title()} requested. In-flight provider work may already have consumed usage."
        )
    except ValueError as exc:
        fail(exc)
    finally:
        store.close()


@app.command()
def pause(run_id: str, data_dir: DataDir = DEFAULT_DATA):
    """Pause without a model call."""
    control(run_id, "pause", data_dir)


@app.command()
def stop(run_id: str, data_dir: DataDir = DEFAULT_DATA):
    """Permanently stop an active run without a model call."""
    control(run_id, "stop", data_dir)


@app.command()
def status(
    run_id: Annotated[str | None, typer.Argument()] = None, data_dir: DataDir = DEFAULT_DATA
):
    """Show a run dashboard, or list saved runs."""
    store = Store(data_dir)
    try:
        if run_id:
            console.print(dashboard(store, run_id))
        else:
            table = Table("Run", "State", "Rounds", "Problem")
            for row in store.runs():
                table.add_row(
                    row["id"], row["status"], str(row["round"]), Text(row["problem"][:90])
                )
            console.print(table)
    except ValueError as exc:
        fail(exc)
    finally:
        store.close()


@app.command()
def watch(run_id: str, data_dir: DataDir = DEFAULT_DATA):
    """Watch from another terminal without occupying the worker lock."""
    store = Store(data_dir)
    try:
        with Live(dashboard(store, run_id), console=console, refresh_per_second=2) as live:
            while True:
                live.update(dashboard(store, run_id))
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        fail(exc)
    finally:
        store.close()


@app.command()
def messages(run_id: str, data_dir: DataDir = DEFAULT_DATA):
    """Read MB's brief, owner questions and steering responses."""
    store = Store(data_dir)
    try:
        run = store.run(run_id)
        console.print(Markdown(run["brief"] or "MB has not prepared the brief yet."))
        for message in store.commands(run_id):
            console.print(
                Text(f"\n{message['kind']} #{message['id']}: {message['text']}", style="cyan")
            )
            console.print(
                Markdown(message["answer"] or "Pending. A worker must be running to answer.")
            )
    except ValueError as exc:
        fail(exc)
    finally:
        store.close()


@app.command("export")
def export_command(run_id: str, output: Path | None = None, data_dir: DataDir = DEFAULT_DATA):
    """Export a Markdown report, citation index and complete JSON history."""
    store = Store(data_dir)
    try:
        path = export_run(store, run_id, output or store.directory / "exports" / run_id)
        console.print(Text(str(path)))
    except (ValueError, OSError) as exc:
        fail(exc)
    finally:
        store.close()


@app.command()
def doctor(config: Path = Path("mariana.toml"), data_dir: DataDir = DEFAULT_DATA):
    """Check client versions, subscription auth and Codex limits; no model prompts."""
    try:
        settings = config_at(config)
        store = Store(data_dir)
        try:
            cwd = store.directory / "client-workspace"
            cwd.mkdir(exist_ok=True)

            async def checks():
                failed = False
                for name, command in (
                    ("Codex", settings.subscription.codex_command),
                    ("Claude", settings.subscription.claude_command),
                ):
                    try:
                        code, out, _ = await capture(executable(command) + ["--version"], cwd)
                        if code:
                            raise ClientError(f"{name} version check failed")
                        console.print(Text(f"{name}: {out.decode(errors='replace').strip()[:100]}"))
                    except (ClientError, OSError, TimeoutError) as exc:
                        console.print(Text(str(exc), style="red"))
                        failed = True
                try:
                    result = await CodexAccount(settings, cwd).snapshot(include_models=True)
                    from marianabot.limits import SubscriptionLimits

                    SubscriptionLimits(store, "openai", settings.subscription).codex(
                        result["limits"]
                    )
                    available = settings.rb.model in result["models"]
                    console.print(
                        f"OpenAI login: {result['auth']}; {settings.rb.model}: {'available' if available else 'not listed'}"
                    )
                    failed |= not available
                except (ClientError, OSError, ValueError) as exc:
                    console.print(Text(str(exc), style="red"))
                    failed = True
                try:
                    result = await claude_account(settings, cwd)
                    console.print(
                        f"Claude login: {result['auth']}; plan: {result['plan'] or 'not reported'}"
                    )
                    console.print(
                        f"{settings.jb.model} access is checked on first use; no fallback is configured."
                    )
                except (ClientError, OSError, TimeoutError) as exc:
                    console.print(Text(str(exc), style="red"))
                    failed = True
                console.print(
                    "Overage-disabled attestation: " + str(settings.subscription.overage_disabled)
                )
                return failed

            failed = asyncio.run(checks())
            if failed:
                raise typer.Exit(2)
        finally:
            store.close()
    except (ValueError, OSError) as exc:
        fail(exc)


if __name__ == "__main__":
    app()
