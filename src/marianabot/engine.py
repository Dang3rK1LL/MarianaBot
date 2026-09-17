import asyncio
import contextlib
import random
import time

from pydantic import ValidationError

from marianabot.clients import ClientError, CodexAccount, DemoClient, NativeClient, claude_account
from marianabot.config import Config
from marianabot.limits import SubscriptionLimits
from marianabot.memory import Compactor, size
from marianabot.prompts import (
    JB_ROLES,
    JUDGE,
    MASTER_ANSWER,
    MASTER_INTAKE,
    MASTER_STEER,
    RB_ROLES,
    SYNTHESIZE,
    Review,
    prompt,
)
from marianabot.store import Store


class Halt(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status


def stop_reason(rounds: list[dict], config: Config) -> str | None:
    if not rounds:
        return None
    current = rounds[-1]
    review = current["review"]
    if review["verdict"] == "needs_human":
        return "Human evidence is required; review the requested experiments"
    same_revision = [r for r in rounds if r["revision"] == current["revision"]]
    cfg = config.research
    if len(same_revision) < cfg.min_rounds:
        return None
    streak = same_revision[-cfg.approval_streak :]
    if len(streak) == cfg.approval_streak and all(
        r["review"]["verdict"] == "approve"
        and not r["review"]["blocking_issues"]
        and r["review"]["score"] >= cfg.quality_threshold
        for r in streak
    ):
        return "Sustained reviewer approval; real-world validation still applies"
    recent = same_revision[-cfg.plateau_rounds :]
    if (
        len(recent) == cfg.plateau_rounds
        and max(r["review"]["score"] for r in recent[1:]) <= recent[0]["review"]["score"]
    ):
        return (
            "Reviewer score has plateaued; further iteration needs a changed brief or new evidence"
        )
    return None


class Engine:
    def __init__(self, store: Store, run_id: str, log=None, clients=None, messages_only=False):
        self.store, self.run_id = store, run_id
        run = store.run(run_id)
        self.config = Config.model_validate_json(run["config"])
        self.demo = bool(run["demo"])
        self.messages_only = messages_only
        self.initial_control_epoch = run["control_epoch"]
        self.deadline = run["created"] + self.config.research.max_hours * 3600
        self.log = log or (lambda _: None)
        self.shutdown = asyncio.Event()
        self.mailbox_lock = asyncio.Lock()
        self.account_lock = asyncio.Lock()
        self.gates = {
            provider: asyncio.Semaphore(brain.concurrency)
            for provider, brain in (("openai", self.config.rb), ("anthropic", self.config.jb))
        }
        self.spacing = {provider: asyncio.Lock() for provider in self.gates}
        self.next_dispatch = dict.fromkeys(self.gates, 0.0)
        self.limits = {
            p: SubscriptionLimits(store, p, self.config.subscription) for p in self.gates
        }
        client_dir = store.directory / "client-workspace"
        client_dir.mkdir(exist_ok=True)
        self.account = CodexAccount(self.config, client_dir)
        self.clients = clients or {
            p: DemoClient()
            if self.demo
            else NativeClient(p, self.config, client_dir, self.limits[p])
            for p in self.gates
        }
        self.memory = Compactor(self)

    def emit(self, message: str):
        self.store.event(self.run_id, message)
        self.log(message)

    def check(self):
        if self.shutdown.is_set():
            raise Halt("paused", "Worker interrupted; resume to continue")
        run = self.store.run(self.run_id)
        control = run["control"]
        if self.messages_only and run["control_epoch"] == self.initial_control_epoch:
            return
        if control:
            raise Halt("stopped" if control == "stop" else "paused", f"Owner requested {control}")
        if time.time() >= self.deadline:
            raise Halt("complete", "Configured elapsed-time limit reached")

    async def wait(self, seconds: float, reason: str):
        self.emit(f"{reason}; wait up to {seconds:.0f}s")
        end = time.time() + seconds
        while time.time() < end:
            self.check()
            await asyncio.sleep(min(1, max(0, end - time.time())))
        self.check()

    async def preflight(self):
        if self.demo:
            return
        if not self.config.subscription.overage_disabled:
            raise Halt(
                "paused",
                "Disable provider extra usage/automatic credit purchases, then set subscription.overage_disabled=true before creating a live run",
            )
        account = await self.refresh_account(include_models=True)
        if self.config.rb.model not in account["models"]:
            raise Halt(
                "paused",
                f"Codex does not list requested model {self.config.rb.model}; no substitute selected",
            )
        await claude_account(self.config, self.account.cwd)
        self.emit(
            "Verified subscription authentication for both clients; MB and RB share OpenAI usage"
        )

    async def refresh_account(self, include_models=False):
        async with self.account_lock:
            account = await self.account.snapshot(include_models=include_models)
            self.limits["openai"].codex(account["limits"])
            return account

    async def monitor_usage(self, interval=60):
        """Refresh account quotas during long calls/waits, without generating model tokens."""
        if self.demo:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                await self.refresh_account()
            except (ClientError, OSError, ValueError):
                # Retain the last good snapshot and its age. Dispatch preflight still gates calls.
                pass

    async def capacity(self, provider: str):
        if self.demo:
            return
        while True:
            self.check()
            if self.limits[provider].remaining():
                await self.wait(
                    self.limits[provider].remaining(), f"{provider} subscription cooling down"
                )
            if provider == "openai":
                await self.refresh_account()
            if self.limits[provider].remaining() <= 0:
                break
        async with self.spacing[provider]:
            delay = self.next_dispatch[provider] - time.time()
            if delay > 0:
                await self.wait(delay, f"{provider} request spacing")
            self.next_dispatch[provider] = (
                time.time() + self.config.subscription.request_spacing_seconds
            )

    async def call(
        self,
        brain: str,
        task: str,
        instruction: str,
        data: dict,
        search: bool = False,
        structured: bool = False,
        internal_compaction: bool = False,
        validate=None,
    ) -> dict:
        self.check()
        cached = self.store.cached(self.run_id, task)
        if cached:
            return cached
        provider = "anthropic" if brain == "JB" else "openai"
        model = self.config.jb.model if brain == "JB" else self.config.rb.model
        actual_search = search and self.config.research.web_search
        max_chars = self.config.research.max_context_chars
        if task == "mb-intake":
            max_chars = max(max_chars, size(data) + 1000)
        elif task.startswith("mb-command-"):
            max_chars = max(max_chars, len(data["owner_message"]) * len(data) + 1000)
        if not internal_compaction:
            if task != "mb-intake":
                data = data | self.memory.context()
            data = await self.memory.prepare(data, max_chars)
        content = prompt(instruction, data, max_chars, actual_search, strict=True)
        attempt = 0
        while True:
            async with self.gates[provider]:
                await self.capacity(provider)
                self.check()
                call_id = self.store.begin_call(self.run_id, task, brain, provider, model)
                self.emit(f"{brain} · {task} · working")
                try:
                    client = self.clients[provider]
                    if isinstance(client, NativeClient):
                        result = await client.complete(
                            content,
                            actual_search,
                            on_usage=lambda usage, final, call_id=call_id: self.store.record_usage(
                                call_id, usage, final
                            ),
                        )
                    else:
                        result = await client.complete(content, actual_search)
                    if structured:
                        raw = result["text"].strip()
                        if raw.startswith(chr(96) * 3):
                            raw = "\n".join(raw.splitlines()[1:-1])
                        result["review"] = Review.model_validate_json(raw).model_dump()
                    if validate:
                        validate(result)
                    result["prompt"] = content
                    self.store.finish(call_id, "done", result)
                    self.emit(f"{brain} · {task} · saved")
                    return result
                except (ValidationError, ValueError):
                    self.store.finish(call_id, "invalid")
                    error = ClientError(
                        "Memory compaction failed validation; originals retained"
                        if internal_compaction
                        else "Judge output failed schema validation",
                        retryable=True,
                    )
                except ClientError as exc:
                    self.store.finish(call_id, "limited" if exc.limited else "unknown")
                    error = exc
                except BaseException:
                    self.store.finish(call_id, "unknown")
                    raise
            if error.limited:
                delay = self.limits[provider].remaining()
                if delay <= 0:
                    self.limits[provider].defer(
                        time.time() + self.config.subscription.unknown_reset_wait_seconds
                    )
                await self.wait(self.limits[provider].remaining(), str(error))
                continue
            if not error.retryable or attempt >= self.config.research.max_retries:
                raise Halt("paused", str(error))
            attempt += 1
            await self.wait(min(120, 2**attempt * 3) + random.random(), str(error))

    async def group(self, calls) -> list[dict]:
        tasks = [asyncio.create_task(call) for call in calls]
        try:
            return await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def handle_commands(self, steering: bool):
        async with self.mailbox_lock:
            await self._handle_commands(steering)

    async def _handle_commands(self, steering: bool):
        for command in self.store.commands(self.run_id, pending_only=True):
            if command["answer"] is not None or (command["kind"] == "steer") != steering:
                continue
            run = self.store.run(self.run_id)
            history = self.store.rounds(self.run_id)
            data = {
                "problem": run["problem"],
                "brief": run["brief"],
                "latest_round": history[-1] if history else "No completed round yet",
                "owner_message": command["text"],
                "conversation": self.store.recent_dialogue(self.run_id),
            }
            instruction = MASTER_STEER if steering else MASTER_ANSWER
            answer = await self.call("MB", f"mb-command-{command['id']}", instruction, data)
            self.store.answer(self.run_id, command, answer["text"])
            await self.memory.advance()
            self.emit(
                f"MB answered {command['kind']} #{command['id']}"
                + ("; brief updated for next round" if steering else "")
            )

    async def mailbox(self):
        while True:
            await self.handle_commands(steering=False)
            await asyncio.sleep(1)

    async def research(self):
        await self.memory.advance()
        run = self.store.run(self.run_id)
        if not run["brief"]:
            intake = await self.call(
                "MB", "mb-intake", MASTER_INTAKE, {"owner_problem": run["problem"]}
            )
            self.store.update_run(self.run_id, brief=intake["text"])
        self.store.message(
            self.run_id, "intake", "MB", "Research brief", self.store.run(self.run_id)["brief"]
        )
        while True:
            self.check()
            await self.handle_commands(steering=True)
            await self.handle_commands(steering=False)
            run = self.store.run(self.run_id)
            history = self.store.rounds(self.run_id)
            if history and history[-1]["revision"] == run["revision"]:
                reason = stop_reason(history, self.config)
                if reason:
                    paused = (
                        history[-1]["review"]["verdict"] == "needs_human" or "plateaued" in reason
                    )
                    raise Halt("paused" if paused else "complete", reason)
            number = run["round"] + 1
            if number > self.config.research.max_rounds:
                raise Halt("complete", "Configured round limit reached")
            self.emit(f"Round {number}/{self.config.research.max_rounds} · RB court")
            prefix = f"round-{number}-revision-{run['revision']}"
            previous = history[-1] if history else {}
            data = {
                "problem": run["problem"],
                "brief": run["brief"],
                "previous_plan": previous.get("plan", "First iteration"),
                "previous_review": previous.get("review", "No previous review"),
            }
            researchers = await self.group(
                [
                    self.call(
                        "RB",
                        f"{prefix}-rb-{i}",
                        RB_ROLES[i % len(RB_ROLES)]
                        + f"\nIndependent proposal {i + 1}. Develop your own recommendation, assumptions, evidence and tests.",
                        data,
                        search=True,
                    )
                    for i in range(self.config.rb.agents)
                ]
            )
            synthesis = await self.call(
                "RB",
                f"{prefix}-synthesis",
                SYNTHESIZE,
                data | {"independent_proposals": [r["text"] for r in researchers]},
            )
            self.store.message(
                self.run_id,
                prefix + "-plan",
                "RB",
                f"Round {number} · Research plan",
                synthesis["text"],
            )
            self.emit(f"Round {number} · JB court")
            judging = {
                "problem": run["problem"],
                "brief": run["brief"],
                "plan": synthesis["text"],
                "previous_review": previous.get("review", {}),
            }
            critics = await self.group(
                [
                    self.call(
                        "JB",
                        f"{prefix}-jb-{i}",
                        JB_ROLES[i % len(JB_ROLES)]
                        + f"\nIndependent critique {i + 1}. Attack this plan with specific evidence and falsifiable objections.",
                        judging,
                        search=True,
                    )
                    for i in range(self.config.jb.agents)
                ]
            )
            chair = await self.call(
                "JB",
                f"{prefix}-verdict",
                JUDGE,
                judging | {"independent_critiques": [r["text"] for r in critics]},
                structured=True,
            )
            self.store.save_round(
                self.run_id, number, run["revision"], synthesis["text"], chair["review"]
            )
            self.store.publish_round(
                self.run_id, number, run["revision"], synthesis["text"], chair["review"]
            )
            await self.memory.advance()
            self.emit(
                f"Round {number} checkpoint · score {chair['review']['score']}/100 · {chair['review']['verdict']}"
            )

    async def run(self):
        self.store.seed_chat(self.run_id)
        if self.store.run(self.run_id)["status"] in ("complete", "stopped"):
            return
        self.store.update_run(self.run_id, status="running", reason="")
        self.store.recover()
        work = mailbox = monitor = None
        try:
            self.check()
            await self.preflight()
            monitor = asyncio.create_task(self.monitor_usage())
            work = asyncio.create_task(self.research())
            mailbox = asyncio.create_task(self.mailbox())
            while True:
                self.check()
                done, _ = await asyncio.wait(
                    [work, mailbox], timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                )
                if done:
                    for task in done:
                        await task
                    break
        except Halt as exc:
            self.store.update_run(self.run_id, status=exc.status, reason=str(exc))
            self.emit(str(exc))
        except (ClientError, OSError, ValueError) as exc:
            message = (
                str(exc)
                if isinstance(exc, ClientError)
                else f"Local {type(exc).__name__}; inspect installation and configuration"
            )
            self.store.update_run(self.run_id, status="paused", reason=message)
            self.emit(message)
        except asyncio.CancelledError:
            self.store.update_run(self.run_id, status="paused", reason="Worker cancelled")
            raise
        except Exception:
            self.store.update_run(
                self.run_id, status="paused", reason="Unexpected worker error; checkpoints retained"
            )
            raise
        finally:
            for task in (work, mailbox, monitor):
                if task:
                    task.cancel()
            for task in (work, mailbox, monitor):
                if task:
                    with contextlib.suppress(asyncio.CancelledError, Halt, ClientError):
                        await task

    async def respond(self):
        """Answer queued questions without restarting paused or finished research."""
        self.store.recover()
        self.store.seed_chat(self.run_id)
        monitor = None
        try:
            self.check()
            await self.preflight()
            monitor = asyncio.create_task(self.monitor_usage())
            await self.memory.advance()
            await self.handle_commands(steering=False)
        except (Halt, ClientError, OSError, ValueError) as exc:
            message = (
                str(exc)
                if isinstance(exc, (Halt, ClientError))
                else f"Local {type(exc).__name__}; inspect installation and configuration"
            )
            self.emit("MB reply interrupted: " + message)
            self.store.message(
                self.run_id,
                f"reply-error-{time.time_ns()}",
                "system",
                "MB could not reply",
                message + "\n\nUse /retry to try pending messages again.",
            )
        finally:
            if monitor:
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor
