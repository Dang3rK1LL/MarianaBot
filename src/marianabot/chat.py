"""A keyboard-first conversation with a research team that outlives the window."""

import asyncio
import json
import re
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Footer, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from marianabot.config import Config, load_config
from marianabot.reports import export_run
from marianabot.store import Store
from marianabot.usage_ui import UsageStrip
from marianabot.worker import WorkerManager, atomic_json

COMMANDS = {
    "/help": "Show commands and keyboard shortcuts",
    "/ask": "Ask MB a question about this research",
    "/steer": "Change the brief at the next round boundary",
    "/pause": "Pause research and save completed work",
    "/resume": "Continue this research from its checkpoints",
    "/retry": "Retry unanswered MB messages",
    "/stop": "End this research run permanently",
    "/new": "Start a new conversation",
    "/sessions": "Browse saved conversations",
    "/open": "Open a conversation by its ID",
    "/status": "Show research progress and recent activity",
    "/usage": "Show the latest reported subscription limits",
    "/export": "Save the plan, conversation and evidence files",
    "/load": "Load a UTF-8 text file into the draft editor",
    "/copy": "Copy the latest research plan",
    "/demo": "Start a free, offline demonstration conversation",
    "/quit": "Close chat; background research continues",
}


def friendly_event(message: str) -> str:
    """Keep internal task IDs in the audit log, out of the conversation UI."""
    match = re.fullmatch(
        r"(RB|JB) · round-(\d+)-revision-\d+-(rb|jb)-(\d+) · (working|saved)", message
    )
    if match:
        brain, number, _, agent, state = match.groups()
        role = "researcher" if brain == "RB" else "critic"
        return f"{brain} · round {number} · {role} {int(agent) + 1} {state}"
    message = re.sub(r"round-(\d+)-revision-\d+-(synthesis|verdict)", r"round \1 chair", message)
    message = message.replace("mb-intake", "your research brief")
    return re.sub(r"mb-command-\d+", "your message", message)


HELP = """## Talk to your research team

Paste or type your business problem and **press Enter**. Include your objective,
constraints, resources and what a useful result would look like. MB makes the brief;
RB develops the plan; JB challenges it. Their replies appear here automatically.

After the first message, ordinary text goes to MB. Use **/steer your instruction**
to change the brief, including answers to MB's initial questions. Steering takes
effect between rounds. MB shares RB's OpenAI allowance and may need to wait for it.

| Command | What it does |
|---|---|
| /ask question | Ask MB without changing research |
| /steer instruction | Revise the next round's brief |
| /pause · /resume | Pause or continue research |
| /stop | Permanently end the run; saved work remains |
| /new · /sessions · /open ID | Start or revisit a conversation |
| /status · /usage | Inspect activity and reported usage |
| /export · /copy | Save all results or copy the latest plan |
| /load path | Load a long problem from a UTF-8 file for editing |
| /retry | Retry pending MB messages after fixing a problem |
| /demo | New offline conversation with fixture responses |
| /quit | Leave chat while background work continues |

**Editing:** Enter sends. Alt+Enter or Ctrl+J adds a newline (Shift+Enter also works
in terminals that support it). Paste keeps all lines in the editor. F7 selects all;
Ctrl+Z undoes. Type `/`, use ↑/↓, then Tab to complete a command. Esc hides suggestions.
Ctrl+L focuses the editor; Ctrl+End jumps to the latest message. F1 opens this help.

Drafts and messages save locally. Closing the window detaches from research;
use /pause first to suspend it. Keep the laptop awake and online for background work.
One research worker runs per data directory. /new opens a draft while an existing
run continues; pause that run before submitting another problem.
"""


class Composer(TextArea):
    BINDINGS = [
        Binding("enter,ctrl+enter", "submit", show=False),
        Binding("alt+enter,shift+enter,ctrl+j", "newline", show=False),
        Binding("tab", "complete", show=False),
        Binding("escape", "hide_commands", show=False),
    ]

    class Submitted(Message):
        pass

    def _on_key(self, event: events.Key):
        if event.key in ("enter", "ctrl+enter"):
            event.stop()
            event.prevent_default()
            self.action_submit()
        elif event.key in ("alt+enter", "shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.action_newline()

    def action_submit(self):
        if self.app.matches and self.text.strip() not in COMMANDS:
            self.app.complete_command()
        else:
            self.post_message(self.Submitted())

    def action_newline(self):
        self.insert("\n")

    def action_complete(self):
        if self.app.matches:
            self.app.complete_command()
        else:
            self.app.action_focus_next()

    def action_hide_commands(self):
        self.app.matches = []
        self.app.query_one("#suggestions").display = False

    def action_cursor_down(self, select=False):
        if self.app.matches:
            self.app.query_one("#suggestions", OptionList).action_cursor_down()
        else:
            super().action_cursor_down(select)

    def action_cursor_up(self, select=False):
        if self.app.matches:
            self.app.query_one("#suggestions", OptionList).action_cursor_up()
        else:
            super().action_cursor_up(select)


class MessageCard(Vertical):
    def __init__(self, role: str, title: str, text: str):
        super().__init__(classes="message " + role.lower())
        self.role, self.heading, self.body = role, title, text

    def compose(self) -> ComposeResult:
        yield Static(Text(f"{self.role.upper()}  ·  {self.heading}"), classes="speaker")
        if len(self.body) > 4500:
            preview = self.body[:1200].rsplit("\n", 1)[0] or self.body[:1200]
            yield Markdown(preview + "\n\n…", open_links=False)
            with Collapsible(
                title=f"Read full message · {len(self.body):,} characters", collapsed=True
            ):
                yield Markdown(self.body, open_links=False)
        else:
            yield Markdown(self.body, open_links=False)


class HelpScreen(ModalScreen):
    BINDINGS = [("escape,f1", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            with VerticalScroll():
                yield Markdown(HELP, open_links=False)
            yield Button("Back to chat · Esc", id="close-help")

    @on(Button.Pressed)
    def close_help(self):
        self.dismiss()


class SessionsScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, runs: list[dict]):
        super().__init__()
        self.runs = runs

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Saved conversations", classes="dialog-title")
            yield Static("Choose with ↑/↓ and Enter. Esc returns to your draft.")
            yield OptionList(
                *[
                    Option(
                        Text(
                            f"{'DEMO · ' if r['demo'] else ''}{r['problem'].replace(chr(10), ' ')[:64]}\n{r['id']}  ·  {r['status']}  ·  round {r['round']}"
                        ),
                        id=r["id"],
                    )
                    for r in self.runs
                ],
                id="session-list",
            )
            if not self.runs:
                yield Static("No saved conversations yet. Send your first problem to begin.")
            yield Button("Back to chat · Esc", id="close-sessions")

    def on_mount(self):
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected):
        self.dismiss(event.option.id)

    @on(Button.Pressed)
    def action_cancel(self):
        self.dismiss(None)


class MarianaChat(App):
    TITLE = "MarianaBot"
    CSS_PATH = "chat.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("f1", "help", "Help", priority=True),
        Binding("ctrl+n", "new", "New", priority=True, show=False),
        Binding("ctrl+o", "sessions", "Sessions", priority=True, show=False),
        Binding("ctrl+l", "compose", "Write", priority=True, show=False),
        Binding("ctrl+end", "latest", "Latest", priority=True),
        Binding("ctrl+q", "detach", "Quit", priority=True),
    ]

    def __init__(self, data_dir: Path, config_path: Path, *, demo=False, run_id=None, manager=None):
        super().__init__()
        self.store = Store(data_dir)
        self.config_path = config_path.resolve()
        self.default_demo = self.demo = demo
        self.manager = manager or WorkerManager(self.store.directory)
        self.state_path = self.store.directory / "chat-state.json"
        try:
            self.saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(self.saved, dict) or not isinstance(
                self.saved.get("drafts", {}), dict
            ):
                self.saved = {}
        except (OSError, ValueError):
            self.saved = {}
        self.run_id = run_id if run_id is not None else self.saved.get("run_id")
        if not self.run_id:
            self.demo = demo or bool(self.saved.get("new_demo", False))
        self.last_message = 0
        self.last_status = None
        self.matches: list[str] = []
        self.dirty = False
        self.submitting = False
        self.awaiting_reply = False
        self.transcript_lock = asyncio.Lock()
        self.exiting = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("MarianaBot", id="brand")
            yield Button("New", id="new")
            yield Button("Sessions", id="sessions")
            yield Button("Help", id="help")
        yield Static("", id="status-line", markup=False)
        yield VerticalScroll(id="conversation")
        yield UsageStrip(id="usage-strip")
        with Vertical(id="compose-box"):
            yield OptionList(id="suggestions", markup=False)
            yield Composer(
                id="composer",
                placeholder="Describe your business problem…",
                highlight_cursor_line=False,
            )
            with Horizontal(id="input-tools"):
                yield Static("Enter send · Alt+Enter newline · / commands", id="input-hint")
                yield Button("Send ↵", id="send")
        yield Footer()

    async def on_mount(self):
        self.chat_screen = self.screen
        self.chat_screen.query_one("#suggestions").display = False
        self.theme = "textual-dark"
        if self.run_id:
            try:
                await self.open_run(self.run_id, save=False)
            except ValueError:
                self.run_id = None
                await self.welcome()
        else:
            await self.welcome()
        self.restore_draft()
        self.action_compose()
        self.resize_layout()
        self.set_interval(0.75, self.refresh_state)
        self.set_interval(0.8, self.save_draft)
        await self.refresh_state()

    def on_resize(self):
        if self.is_mounted:
            self.call_after_refresh(self.resize_layout)

    def resize_layout(self):
        editor = self.chat_screen.query_one(Composer)
        maximum = 8 if self.size.height >= 32 else 5
        editor.styles.height = min(maximum, max(3, editor.wrapped_document.height + 2))
        self.chat_screen.query_one("#suggestions").styles.max_height = (
            6 if self.size.height >= 32 else 3
        )

    async def welcome(self):
        self.chat_screen.query_one(Composer).placeholder = "Describe your business problem…"
        await self.chat_screen.query_one("#conversation").remove_children()
        await self.chat_screen.query_one("#conversation").mount(
            Static("What are you working on?", id="welcome-title"),
            Static(
                "Describe the decision, business idea or problem.\n"
                "Add your constraints and what a useful result would look like.\n\n"
                "Paste a long brief here, or use /load to open a text file.",
                id="welcome-copy",
            ),
        )

    async def add_card(self, role: str, title: str, text: str):
        if not self.is_running or self.exiting:
            return
        transcript = self.chat_screen.query_one("#conversation", VerticalScroll)
        follow = transcript.is_vertical_scroll_end
        await transcript.mount(MessageCard(role, title, text))
        if follow:
            self.call_after_refresh(transcript.scroll_end, animate=False)

    async def note(self, title: str, text: str):
        if self.run_id:
            self.store.message(self.run_id, "notice-" + uuid.uuid4().hex, "system", title, text)
            await self.sync_messages()
        else:
            await self.add_card("system", title, text)

    async def open_run(self, run_id: str, *, save=True):
        run = self.store.run(run_id)
        if self.default_demo and not run["demo"]:
            raise ValueError(
                "This window is in offline demo mode. Open a normal chat for live sessions."
            )
        if save:
            self.save_draft(force=True)
        self.run_id, self.demo = run_id, bool(run["demo"])
        self.chat_screen.query_one(
            Composer
        ).placeholder = "Ask MB about this research, or /steer to change direction…"
        self.last_message, self.last_status = 0, None
        self.awaiting_reply = False
        self.store.seed_chat(run_id)
        await self.chat_screen.query_one("#conversation").remove_children()
        await self.sync_messages()
        self.restore_draft()
        self.dirty = True
        self.action_compose()

    async def sync_messages(self):
        if not self.run_id or not self.is_running or self.exiting:
            return
        async with self.transcript_lock:
            for message in self.store.messages(self.run_id, self.last_message):
                await self.add_card(message["role"], message["title"], message["text"])
                self.last_message = message["id"]

    def draft_key(self):
        return self.run_id or ("new-demo" if self.demo else "new")

    def restore_draft(self):
        draft = self.saved.get("drafts", {}).get(self.draft_key(), "")
        self.chat_screen.query_one(Composer).load_text(draft if isinstance(draft, str) else "")

    def save_draft(self, force=False):
        if (
            not self.is_mounted
            or not self.chat_screen.query(Composer)
            or (not self.dirty and not force)
        ):
            return
        self.saved["run_id"] = self.run_id
        self.saved["new_demo"] = self.demo
        self.saved.setdefault("drafts", {})[self.draft_key()] = self.chat_screen.query_one(
            Composer
        ).text
        try:
            atomic_json(self.state_path, self.saved)
            self.dirty = False
        except OSError:
            self.notify("Draft could not be saved. Check available disk space.", severity="error")

    @on(TextArea.Changed)
    def editor_changed(self):
        self.dirty = True
        value = self.chat_screen.query_one(Composer).text
        self.matches = (
            [key for key in COMMANDS if key.startswith(value)]
            if value.startswith("/") and not any(c.isspace() for c in value)
            else []
        )
        options = self.chat_screen.query_one("#suggestions", OptionList)
        options.clear_options()
        options.add_options(
            [Option(Text(f"{key:<11} {COMMANDS[key]}"), id=key) for key in self.matches]
        )
        options.highlighted = 0 if self.matches else None
        options.display = bool(self.matches)
        limit = 20000 if self.run_id else 100000
        self.chat_screen.query_one("#input-hint", Static).update(
            "Enter send · Alt+Enter newline · "
            + (f"{len(value):,}/{limit:,}" if value else "/ commands")
        )
        self.call_after_refresh(self.resize_layout)

    def complete_command(self):
        options = self.chat_screen.query_one("#suggestions", OptionList)
        if self.matches:
            key = self.matches[options.highlighted or 0]
            editor = self.chat_screen.query_one(Composer)
            editor.load_text(key + " ")
            editor.move_cursor(editor.document.end)
            editor.focus()

    @on(OptionList.OptionSelected, "#suggestions")
    def suggestion_chosen(self):
        self.complete_command()

    @on(Composer.Submitted)
    @on(Button.Pressed, "#send")
    async def submit(self):
        if self.submitting:
            return
        editor = self.chat_screen.query_one(Composer)
        original = editor.text
        text = original.strip()
        if not text:
            return
        self.submitting = True
        self.chat_screen.query_one("#send", Button).disabled = True
        # Clear before the asynchronous launch so new typing is never discarded.
        editor.clear()
        self.save_draft(force=True)
        self.run_worker(self.process_submission(original), group="submission", exit_on_error=False)

    async def process_submission(self, original: str):
        editor = self.chat_screen.query_one(Composer)
        text = original.strip()
        try:
            if text.startswith("/"):
                # Split only the first whitespace, preserving multiline arguments.
                parts = text.split(maxsplit=1)
                command, argument = parts[0].lower(), parts[1] if len(parts) > 1 else ""
                await self.command(command, argument)
            elif not self.run_id:
                await self.start_problem(text)
            else:
                await self.send_message("ask", text)
        except (ValueError, OSError) as exc:
            if not editor.text:
                editor.load_text(original)
            await self.note("Could not complete that action", str(exc))
        finally:
            self.submitting = False
            if self.is_running and not self.exiting:
                self.chat_screen.query_one("#send", Button).disabled = False
                self.dirty = True
                self.action_compose()
                await self.refresh_state()

    async def start_problem(self, problem: str):
        if self.manager.active():
            raise ValueError(
                "Another session is still working. Open /sessions and /pause it before starting this problem. Your draft is kept."
            )
        if self.demo:
            config = Config()
            config.research.max_rounds = 2
            config.research.min_rounds = 1
            config.rb.concurrency = config.jb.concurrency = 2
        else:
            if not self.config_path.exists():
                raise ValueError(
                    "Configuration is missing. Run mariana init and mariana doctor once; then reopen chat. /demo works without setup."
                )
            config = load_config(self.config_path)
            if not config.subscription.overage_disabled:
                raise ValueError(
                    "Subscription setup is incomplete. Disable extra usage and automatic credit purchases in both accounts, then set subscription.overage_disabled=true in mariana.toml. /demo needs no account setup."
                )
        run_id = self.store.create_run(problem, config, demo=self.demo)
        await self.open_run(run_id)
        await self.note(
            "Research started",
            "MB is preparing your brief. Replies will appear here as each stage finishes. You can keep writing; use **/steer** to change direction or **/pause** to take a break.",
        )
        try:
            await self.manager.start(run_id)
        except (ValueError, OSError) as exc:
            await self.note(
                "Problem saved; worker not connected",
                str(exc) + "\n\nUse **/resume** to start this saved problem.",
            )

    async def send_message(self, kind: str, text: str):
        self.require_run()
        active = self.manager.active()
        if active and active.get("run_id") != self.run_id:
            raise ValueError(
                "Another conversation is using the worker. Pause it before asking MB here."
            )
        self.store.enqueue(self.run_id, kind, text)
        await self.sync_messages()
        if kind == "steer":
            await self.note(
                "Steering queued",
                "MB will update the brief before the next round."
                + (" Research is paused; use **/resume** when ready." if not active else ""),
            )
        else:
            self.awaiting_reply = True
            try:
                await self.manager.start(self.run_id, messages_only=True)
            except (ValueError, OSError) as exc:
                self.awaiting_reply = False
                await self.note(
                    "Message saved", str(exc) + "\n\nUse **/retry** to send pending messages to MB."
                )

    def require_run(self):
        if not self.run_id:
            raise ValueError("Send a business problem first, or use /sessions to open saved work.")

    async def command(self, name: str, argument: str):
        if name not in COMMANDS:
            raise ValueError(f"Unknown command: {name}. Type / for suggestions or use /help.")
        if name not in ("/ask", "/steer", "/open", "/load") and argument:
            raise ValueError(f"{name} takes no argument.")
        if name == "/help":
            self.action_help()
        elif name in ("/new", "/demo"):
            await self.new_conversation(name == "/demo" or self.default_demo)
        elif name == "/sessions":
            self.action_sessions()
        elif name == "/open":
            await self.open_run(argument)
        elif name == "/quit":
            self.action_detach()
        elif name == "/load":
            path = Path(argument.strip().strip('"')).expanduser()
            if not argument or not path.is_file():
                raise ValueError("Use /load followed by the path to a UTF-8 text file.")
            if path.stat().st_size > 400000:
                raise ValueError("This file is too large. Maximum draft: 100,000 characters.")
            content = path.read_text(encoding="utf-8-sig")
            limit = 20000 if self.run_id else 100000
            if len(content) > limit:
                raise ValueError(f"Maximum draft in this conversation: {limit:,} characters.")
            self.chat_screen.query_one(Composer).load_text(content)
            self.notify("File loaded into your draft. Edit it, then press Enter.")
        else:
            self.require_run()
            if name in ("/ask", "/steer"):
                await self.send_message(name[1:], argument)
            elif name in ("/pause", "/stop"):
                run = self.store.run(self.run_id)
                active = self.manager.active()
                if (
                    active
                    and active.get("run_id") == self.run_id
                    and active.get("mode") == "messages"
                ):
                    self.awaiting_reply = False
                    self.store.update_run(self.run_id, control=name[1:])
                    await self.note(
                        "MB cancellation requested",
                        "The reply is being cancelled. Research status is unchanged. Use **/retry** when you want MB to answer pending messages.",
                    )
                    return
                if run["status"] in ("stopped", "complete"):
                    raise ValueError(
                        f"Research is already {run['status']}. You can still ask MB about it."
                    )
                self.store.update_run(
                    self.run_id,
                    control=name[1:],
                    status="paused" if name == "/pause" else "stopped",
                    reason="Owner requested " + name[1:],
                )
                await self.note(
                    "Pause requested" if name == "/pause" else "Research ended",
                    "Completed work is saved. Active requests are being cancelled. "
                    + (
                        "Use **/resume** to continue."
                        if name == "/pause"
                        else "Use **/new** for a fresh research run."
                    ),
                )
            elif name == "/resume":
                if self.store.run(self.run_id)["status"] in ("complete", "stopped"):
                    raise ValueError(
                        "This research is closed. Use /new for another run; /export saves this plan."
                    )
                await self.manager.start(self.run_id)
                await self.note(
                    "Research connected",
                    "The worker is continuing this session. Completed calls are reused.",
                )
            elif name == "/retry":
                self.awaiting_reply = True
                await self.manager.start(self.run_id, messages_only=True)
            elif name in ("/status", "/usage"):
                content = self.usage_text() if name == "/usage" else self.status_text()
                await self.note(name[1:].title(), content)
            elif name == "/export":
                # A distinct destination avoids racing the worker's automatic exports.
                target = (
                    self.store.directory
                    / "exports"
                    / self.run_id
                    / ("snapshot-" + uuid.uuid4().hex[:8])
                )
                report = export_run(self.store, self.run_id, target)
                await self.note(
                    "Export saved",
                    "Report, conversation, full call history and citations:\n\n"
                    + str(report.parent),
                )
            elif name == "/copy":
                rounds = self.store.rounds(self.run_id)
                if not rounds:
                    raise ValueError("There is no completed research round yet.")
                self.copy_to_clipboard(rounds[-1]["plan"])
                self.notify("Latest plan copied (requires terminal clipboard support).")

    async def new_conversation(self, demo: bool):
        self.save_draft(force=True)
        self.run_id, self.demo = None, demo
        self.last_message, self.last_status = 0, None
        self.awaiting_reply = False
        await self.welcome()
        self.restore_draft()
        self.dirty = True
        self.action_compose()

    def usage_text(self):
        if self.demo:
            return "Offline demo · no subscription usage."
        lines = []
        for provider, label in (("openai", "OpenAI · MB + RB"), ("anthropic", "Claude · JB")):
            data = self.store.get_limits(provider)
            lines.append(label)
            if not data.get("windows"):
                lines.append("No reported snapshot yet.")
            for window in data.get("windows", []):
                percent = window.get("percent")
                used = (
                    f"{percent:.0f}% used"
                    if percent is not None
                    else window.get("status", "unknown")
                )
                reset = (
                    datetime.fromtimestamp(window["reset"]).strftime("%d %b %H:%M")
                    if window.get("reset")
                    else "unknown"
                )
                lines.append(f"{window['name']}: {used}\nReset: {reset}")
            remaining = data.get("until", 0) - time.time()
            if remaining > 0:
                lines.append(f"Waiting · {remaining / 60:.0f} min until retry")
            if data.get("observed"):
                lines.append(
                    "Observed " + datetime.fromtimestamp(data["observed"]).strftime("%d %b %H:%M")
                )
            lines.append("")
        return (
            "\n".join(lines) + "\nSnapshots are provider-reported; unknown does not mean unlimited."
        )

    def status_text(self):
        run = self.store.run(self.run_id)
        events = self.store.events(self.run_id, 8)
        return (
            f"Session {self.run_id}\n\n**{run['status']} · round {run['round']} · brief revision {run['revision']}**\n\n{run['reason']}\n\n"
            + "\n\n".join(friendly_event(e["text"]) for e in reversed(events))
        )

    async def refresh_state(self):
        if not self.is_running or self.exiting or not self.chat_screen.query(Composer):
            return
        await self.sync_messages()
        if not self.is_running or self.exiting:
            return
        active = self.manager.active()
        usage_run = active.get("run_id") if active else None
        usage_run = usage_run or self.run_id
        usage_demo = bool(self.store.run(usage_run)["demo"]) if usage_run else self.demo
        scope = (
            "This run"
            if usage_run == self.run_id and usage_run
            else f"Working run {usage_run}"
            if usage_run
            else "Usage"
        )
        self.chat_screen.query_one(UsageStrip).update_usage(
            self.store.usage_totals(usage_run) if usage_run else {},
            {provider: self.store.get_limits(provider) for provider in ("openai", "anthropic")},
            scope=scope,
            demo=usage_demo,
            working=bool(active and active.get("run_id") == usage_run),
        )
        mode = "Offline demo" if self.demo else "Subscriptions"
        status = "New conversation"
        if self.run_id:
            run = self.store.run(self.run_id)
            status = f"{run['status'].capitalize()} · round {run['round']}"
            attached = active and active.get("run_id") == self.run_id
            if run["status"] == "running" and not attached:
                status = "Worker disconnected · /resume to reconnect"
            pending = self.store.pending_questions(self.run_id)
            if pending:
                status += f" · {pending} MB pending"
            else:
                self.awaiting_reply = False
            # A reply enqueued as research finishes must not be stranded at the boundary.
            if self.awaiting_reply and pending and not active and not self.submitting:
                self.awaiting_reply = False
                self.run_worker(
                    self.ensure_reply(), group="reply-start", exclusive=True, exit_on_error=False
                )
            if self.last_status != (run["status"], run["reason"]):
                self.last_status = (run["status"], run["reason"])
                if run["status"] in ("paused", "complete", "stopped") and run["reason"]:
                    self.store.message(
                        self.run_id,
                        f"status-{run['status']}-{run['round']}-{run['revision']}-{run['reason']}",
                        "system",
                        "Research " + run["status"],
                        run["reason"],
                    )
                    await self.sync_messages()
                    if not self.is_running or self.exiting:
                        return
            activity = self.store.activity(self.run_id)
            busy = []
            for brain, label in (
                ("MB", "MB replying"),
                ("RB", "RB researching"),
                ("JB", "JB reviewing"),
            ):
                working = (
                    sum(
                        a["count"]
                        for a in activity
                        if a["brain"] == brain and a["state"] == "running"
                    )
                    if attached
                    else 0
                )
                if working:
                    busy.append(label + (f" ({working})" if working > 1 else ""))
            if busy:
                status += " · " + " · ".join(busy)
        else:
            if active:
                status += " · another session working · /sessions"
        self.chat_screen.query_one("#status-line", Static).update(f"{status} · {mode}")

    async def ensure_reply(self):
        try:
            await self.manager.start(self.run_id, messages_only=True)
        except (ValueError, OSError) as exc:
            await self.note("MB message pending", str(exc) + " Use /retry when ready.")

    @on(Button.Pressed, "#help")
    def action_help(self):
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss()
            return
        self.push_screen(HelpScreen())

    @on(Button.Pressed, "#sessions")
    def action_sessions(self):
        async def chosen(run_id):
            if run_id:
                await self.open_run(run_id)
            self.action_compose()

        runs = [r for r in self.store.runs() if r["demo"] or not self.default_demo]
        self.push_screen(SessionsScreen(runs), chosen)

    @on(Button.Pressed, "#new")
    async def action_new(self):
        await self.new_conversation(self.default_demo)

    def action_compose(self):
        self.chat_screen.query_one(Composer).focus()

    def action_latest(self):
        self.chat_screen.query_one("#conversation", VerticalScroll).scroll_end(animate=False)

    def action_detach(self):
        self.save_draft(force=True)
        self.exiting = True
        active = self.manager.active()
        self.exit(
            "Chat closed. Research continues in the background; reopen MarianaBot to reconnect."
            if active
            else "Conversation saved. See you next time."
        )

    def on_unmount(self):
        # on_shutdown is too late to query the composer; also save on normal unmount.
        if self.chat_screen.query(Composer):
            self.save_draft(force=True)
        self.store.close()

    @on(Markdown.LinkClicked)
    def open_link(self, event: Markdown.LinkClicked):
        if urlparse(event.href).scheme in ("http", "https"):
            webbrowser.open(event.href)
