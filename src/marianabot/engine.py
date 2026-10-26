import asyncio
import contextlib
import json
import random
import time

from pydantic import ValidationError

from marianabot.clients import (
    ClaudeAccount,
    ClientError,
    CodexAccount,
    NativeClient,
    claude_account,
    claude_models,
)
from marianabot.config import Config
from marianabot.guidance import (
    INTAKE,
    QUESTION_AUDIT,
    QUESTION_PROMPT,
    Intake,
    QuestionAudit,
    Questions,
    validate_questions,
)
from marianabot.limits import SubscriptionLimits
from marianabot.memory import Compactor, size
from marianabot.model_catalog import parse_models
from marianabot.presentation import update_metadata
from marianabot.prompts import (
    JB_ROLES,
    JUDGE,
    MASTER_ANSWER,
    MASTER_STEER,
    PHASES,
    RB_ROLES,
    SYNTHESIZE,
    Review,
    prompt,
)
from marianabot.reports import export_run
from marianabot.routing import TaskRouter, phase_for
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
    same_revision = [r for r in rounds if r["revision"] == current["revision"]]
    cfg = config.research
    if len(same_revision) < cfg.min_rounds:
        return None
    stable = same_revision[-cfg.stable_rounds :]
    if (
        len(stable) == cfg.stable_rounds
        and all(
            row["review"].get("decision_ready")
            and row["review"].get("decision", "").strip().casefold()
            == review.get("decision", "").strip().casefold()
            and row["review"].get("deliverables")
            and not row["review"].get("blocking_issues")
            and all(
                row["review"].get(key, True)
                for key in (
                    "scope_aligned",
                    "owner_constraints_preserved",
                    "online_only",
                    "guidance_respected",
                )
            )
            for row in stable
        )
        and review.get("decision")
        and not review.get("material_change", True)
    ):
        return "Stable decision reached; requested deliverables and remaining gaps are disclosed"
    streak = same_revision[-cfg.approval_streak :]
    if len(streak) == cfg.approval_streak and all(
        r["review"]["verdict"] == "approve"
        and r["review"].get("foundation_ready", False)
        and all(
            r["review"].get(key, True)
            for key in ("scope_aligned", "owner_constraints_preserved", "online_only")
        )
        and not r["review"]["blocking_issues"]
        and r["review"]["score"] >= cfg.quality_threshold
        for r in streak
    ):
        return "Online research conclusion supported across reviews; remaining limitations are disclosed"
    recent = same_revision[-cfg.plateau_rounds :]
    if (
        len(recent) == cfg.plateau_rounds
        and review.get("foundation_ready", False)
        and not review.get("online_checks")
        and max(r["review"]["score"] for r in recent[1:]) <= recent[0]["review"]["score"]
    ):
        return "Available online evidence has stopped changing the conclusion; remaining gaps are disclosed"
    return None


class Engine:
    def __init__(self, store: Store, run_id: str, log=None, messages_only=False):
        self.store, self.run_id = store, run_id
        run = store.run(run_id)
        self.config = Config.model_validate_json(run["config"])
        if run["demo"]:
            raise ValueError(
                "Historical simulated records are read-only; live research requires a real run"
            )
        self.messages_only = messages_only
        self.initial_control_epoch = run["control_epoch"]
        self.deadline = (
            run["created"]
            + self.config.research.max_hours * 3600
            + store.guidance_wait_seconds(run_id)
        )
        self.log = log or (lambda _: None)
        self.shutdown = asyncio.Event()
        self.mailbox_lock = asyncio.Lock()
        self.account_lock = asyncio.Lock()
        self.claude_account_lock = asyncio.Lock()
        self.mb_gate = asyncio.Semaphore(1)
        self.router = TaskRouter(self.config)
        self.gates = {
            provider: asyncio.Semaphore(brain.concurrency)
            for provider, brain in (("openai", self.config.rb), ("anthropic", self.config.jb))
        }
        self.spacing = {provider: asyncio.Lock() for provider in self.gates}
        self.next_dispatch = dict.fromkeys(self.gates, 0.0)
        self.limits = {
            p: SubscriptionLimits(store, p, self.config.subscription) for p in self.gates
        }
        client_dir = store.client_directory(run_id)
        client_dir.mkdir(parents=True, exist_ok=True)
        self.account = CodexAccount(self.config, client_dir)
        self.claude_account = ClaudeAccount(self.config, client_dir)
        self.clients = {
            p: NativeClient(p, self.config, client_dir, self.limits[p]) for p in self.gates
        }
        self.memory = Compactor(self)

    def emit(self, message: str, *, kind: str = "", payload: dict | None = None):
        self.store.event(self.run_id, message, kind=kind, payload=payload)
        self.log(message)

    def stage(self, stage: str, number: int = 0, **details):
        run = self.store.run(self.run_id)
        self.emit(
            f"Research stage · {stage} · round {number}",
            kind="research_stage",
            payload={
                "stage": stage,
                "number": number,
                "revision": run["revision"],
                "elapsed": time.time() - run["created"],
                "usage": self.store.usage_totals(self.run_id),
                **details,
            },
        )

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
        if seconds >= 60:
            self.emit(
                reason,
                kind="research_wait",
                payload={"reason": reason, "seconds": seconds, "resumed": False},
            )
        end = time.time() + seconds
        while time.time() < end:
            self.check()
            await asyncio.sleep(min(1, max(0, end - time.time())))
        self.check()
        if seconds >= 60:
            self.emit(
                "Research wait finished",
                kind="research_wait",
                payload={"reason": reason, "seconds": seconds, "resumed": True},
            )

    async def preflight(self):
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
        self.router.set_catalog("rb", parse_models("rb", account["model_details"]))
        await claude_account(self.config, self.account.cwd)
        self.router.set_catalog(
            "jb", parse_models("jb", await claude_models(self.config, self.account.cwd))
        )
        if self.config.jb.model not in self.router.catalogs["jb"]:
            raise Halt(
                "paused",
                "Selected Claude ceiling is unavailable or excluded from subscription research",
            )
        with contextlib.suppress(ClientError, OSError, ValueError):
            await self.refresh_claude_account()
        self.emit(
            "Verified subscription authentication for both clients; MB and RB share OpenAI usage"
        )

    async def refresh_account(self, include_models=False):
        async with self.account_lock:
            for attempt in range(self.config.research.max_retries + 1):
                try:
                    account = await self.account.snapshot(include_models=include_models)
                    self.limits["openai"].codex(account["limits"])
                    if attempt:
                        self.emit("Codex usage connection restored", kind="account_check_recovered")
                    return account
                except ClientError as exc:
                    retrying = exc.retryable and attempt < self.config.research.max_retries
                    self.emit(
                        str(exc),
                        kind="account_check_failed",
                        payload={
                            "error": str(exc),
                            "diagnostics": exc.diagnostics,
                            "retrying": retrying,
                            "attempt": attempt + 1,
                        },
                    )
                    if not retrying:
                        if attempt:
                            final = ClientError(f"{exc} (after {attempt + 1} usage-check attempts)")
                            final.diagnostics = exc.diagnostics | {"attempts": attempt + 1}
                            raise final from exc
                        raise
                    await self.wait(min(30, 2**attempt * 3), "Retrying Codex usage check")

    async def monitor_usage(self, interval=60):
        """Refresh account quotas during long calls/waits, without generating model tokens."""

        async def refresh(check):
            try:
                await check()
            except (ClientError, OSError, ValueError):
                # Retain the last good snapshot and its age. Dispatch preflight still gates calls.
                pass

        while True:
            await asyncio.sleep(interval)
            await asyncio.gather(
                refresh(self.refresh_account), refresh(self.refresh_claude_account)
            )

    async def refresh_claude_account(self):
        async with self.claude_account_lock:
            account = await self.claude_account.snapshot()
            self.limits["anthropic"].claude_snapshot(
                account["limits"], observed=account.get("observed"), model=self.config.jb.model
            )
            return account

    async def capacity(self, provider: str):
        while True:
            self.check()
            if self.limits[provider].remaining():
                await self.wait(
                    self.limits[provider].remaining(), f"{provider} subscription cooling down"
                )
            if provider == "openai":
                await self.refresh_account()
            else:
                with contextlib.suppress(ClientError, OSError, ValueError):
                    await self.refresh_claude_account()
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
        validate=None,
        schema=None,
    ) -> dict:
        self.check()
        cached = self.store.cached(self.run_id, task)
        if cached:
            return cached
        words = min(
            self.config.research.max_response_words,
            250 if brain == "MB" else 450 if "-rb-" in task or "-jb-" in task else 700,
        )
        for candidate in self.store.length_rejections(self.run_id, task):
            result = candidate["result"]
            try:
                self.validate_output(
                    result, words, structured=structured, validate=validate, schema=schema
                )
            except (ValidationError, ValueError):
                continue
            result["recovered_validation_error"] = result.pop("validation_error")
            self.store.finish(candidate["id"], "done", result, record_usage=False)
            self.emit(
                f"{brain} · {task} · saved response recovered; no new model call",
                kind="output_recovered",
                payload={"call_id": candidate["id"], "task": task, "brain": brain},
            )
            return result
        provider = "anthropic" if brain == "JB" else "openai"
        actual_search = search and self.config.research.web_search
        max_chars = self.config.research.max_context_chars
        if task in ("mb-intake", "mb-contract"):
            max_chars = max(max_chars, size(data) + 1000)
        else:
            data = data | self.memory.context()
        data = await self.memory.prepare(data, max_chars)
        history = self.store.rounds(self.run_id)
        revision = self.store.run(self.run_id)["revision"]
        phase = phase_for(history, revision)
        current = [row for row in history if row["revision"] == revision]
        difficult = bool(current and current[-1]["review"].get("score", 100) < 55)
        if len(current) >= 2 and current[-1]["review"].get("blocking_issues"):
            difficult |= current[-1]["review"].get("score", 100) <= current[-2]["review"].get(
                "score", 0
            )
        instruction += f"\nCurrent phase: {phase}. {PHASES[phase]}\nRESPONSE_BUDGET: at most {words} words. Return only the concise requested result."
        if schema:
            instruction += "\nReturn only JSON matching this schema:\n" + json.dumps(
                schema.model_json_schema()
            )
        content = prompt(instruction, data, max_chars, actual_search)
        request_content = content
        attempt = 0
        quality_retry = 0
        while True:
            try:
                selection = self.router.select(
                    brain, task, phase=phase, difficult=difficult, retry=quality_retry
                )
            except ValueError as exc:
                raise Halt("paused", str(exc)) from exc
            gate = self.mb_gate if brain == "MB" else self.gates[provider]
            async with gate:
                await self.capacity(provider)
                self.check()
                call_id = self.store.begin_call(
                    self.run_id, task, brain, provider, selection.model, selection.effort
                )
                self.emit(
                    f"{brain} · {selection.model} / {selection.effort} · {selection.reason}",
                    kind="model_selected",
                    payload={
                        "brain": brain,
                        "model": selection.model,
                        "effort": selection.effort,
                        "reason": selection.reason,
                        "task": task,
                    },
                )
                self.emit(f"{brain} · {task} · working")
                call_content = request_content
                result = None
                request_started = time.monotonic()
                try:
                    client = self.clients[provider]
                    request_config = self.config.model_copy(deep=True)
                    selected_brain = request_config.jb if brain == "JB" else request_config.rb
                    selected_brain.model, selected_brain.effort = (
                        selection.model,
                        selection.effort,
                    )
                    if brain == "MB":
                        request_config.research.request_timeout_seconds = min(
                            120, request_config.research.request_timeout_seconds
                        )
                    client = NativeClient(
                        provider, request_config, client.cwd, self.limits[provider]
                    )
                    result = await client.complete(
                        call_content,
                        actual_search,
                        on_usage=lambda usage, final, call_id=call_id: self.store.record_usage(
                            call_id, usage, final
                        ),
                    )
                    result["elapsed_seconds"] = time.monotonic() - request_started
                    result["selection_reason"] = selection.reason
                    if task.endswith("-synthesis"):
                        result["text"], result["update"] = update_metadata(result["text"])
                    self.validate_output(
                        result,
                        words,
                        structured=structured,
                        validate=validate,
                        schema=schema,
                    )
                    result["prompt"] = call_content
                    self.store.finish(call_id, "done", result)
                    self.emit(f"{brain} · {task} · saved")
                    return result
                except (ValidationError, ValueError) as exc:
                    quality_retry += 1
                    message = (
                        str(exc)
                        if not isinstance(exc, ValidationError)
                        else "Output failed the requested schema"
                    )
                    request_content = (
                        content
                        + f"\nOUTPUT_RETRY: {message}. Return corrected JSON matching the exact supplied schema where requested. Keep it concise. Do not add unrelated fields or assign validation work to the owner."
                    )
                    rejected = dict(result or {}) | {
                        "prompt": call_content,
                        "validation_error": message,
                    }
                    self.store.finish(call_id, "invalid", rejected)
                    error = ClientError(message, retryable=True)
                except ClientError as exc:
                    self.store.finish(
                        call_id,
                        "limited" if exc.limited else "unknown",
                        {
                            "prompt": call_content,
                            "error": str(exc),
                            "retryable": exc.retryable,
                            "limited": exc.limited,
                            "diagnostics": exc.diagnostics,
                        },
                    )
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

    def validate_output(
        self,
        result: dict,
        words: int,
        *,
        structured=False,
        validate=None,
        schema=None,
    ):
        if schema:
            raw = result["text"].strip()
            if raw.startswith(chr(96) * 3):
                raw = "\n".join(raw.splitlines()[1:-1])
            result["parsed"] = schema.model_validate_json(raw).model_dump()
            if schema is Intake:
                result["text"] = result["parsed"]["brief"]
        if structured:
            raw = result["text"].strip()
            if raw.startswith(chr(96) * 3):
                raw = "\n".join(raw.splitlines()[1:-1])
            result["review"] = Review.model_validate_json(raw).model_dump()
            if result["review"]["verdict"] == "needs_human":
                result["review"]["verdict"] = "revise"
        if validate:
            validate(result)
        self.bound_output(result, words, structured=structured)

    def bound_output(self, result: dict, target_words: int, *, structured=False):
        """Length is presentation, not a failed research or schema validation."""
        text = result["text"]
        count = len(text.split())
        limit = self.config.research.max_response_words
        if count <= target_words and len(text) <= limit * 14:
            return
        oversized = count > limit or len(text) > limit * 14
        action = "within_limit"
        if oversized:
            if structured:
                # Keep validated fields intact: trimming JSON or an objection can
                # change the verdict's meaning. Schema validation happens first.
                action = "structured_retained"
            else:
                action = "response_retained"
        result["output_budget"] = {
            "target_words": target_words,
            "max_words": limit,
            "original_words": count,
            "original_chars": len(text),
            "action": action,
        }
        if action != "within_limit":
            self.emit(
                "Length target exceeded; "
                + (
                    "full provider response retained"
                    if action == "response_retained"
                    else "validated judge fields retained"
                ),
                kind="output_budget",
                payload=result["output_budget"],
            )

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
                "owner_message": command["text"],
                "conversation": self.store.recent_dialogue(self.run_id),
            }
            if not steering:
                last = history[-1] if history else None
                data["latest_round"] = (
                    {
                        "number": last["number"],
                        "review": last["review"],
                        "plan_excerpt": last["plan"][:3000],
                        "excerpt_notice": "An excerpt only; the complete plan is saved in the run archive.",
                    }
                    if last
                    else "No completed round yet"
                )
            instruction = MASTER_STEER if steering else MASTER_ANSWER
            answer = await self.call("MB", f"mb-command-{command['id']}", instruction, data)
            self.store.answer(self.run_id, command, answer["text"])
            await self.memory.advance()
            self.emit(
                f"MB answered {command['kind']} #{command['id']}"
                + ("; owner instruction saved for next round" if steering else "")
            )

    async def mailbox(self):
        while True:
            await self.handle_commands(steering=False)
            await asyncio.sleep(1)

    async def round_questions(self, number: int, history: list[dict]) -> list[dict]:
        existing = self.store.guidance(self.run_id, number)
        if not existing:
            self.stage("questions", number)
            earlier = self.store.guidance(self.run_id)
            run = self.store.run(self.run_id)
            data = {
                "problem": run["problem"],
                "brief": run["brief"],
                "prior_questions": earlier,
                "previous_review": history[-1]["review"] if history else {},
                "question_limit": self.config.research.questions_per_round,
            }
            feedback = []
            for attempt in range(self.config.research.max_retries + 1):

                def validate(result):
                    validate_questions(
                        result["parsed"], earlier, self.config.research.questions_per_round
                    )

                proposed = await self.call(
                    "MB",
                    f"mb-guidance-{number}-attempt-{attempt}",
                    QUESTION_PROMPT,
                    data | {"audit_feedback": feedback},
                    schema=Questions,
                    validate=validate,
                )
                audit = await self.call(
                    "JB",
                    f"guidance-audit-{number}-attempt-{attempt}",
                    QUESTION_AUDIT,
                    data | {"proposed_questions": proposed["parsed"]["questions"]},
                    schema=QuestionAudit,
                )
                judged = audit["parsed"]
                if all(
                    judged[key]
                    for key in (
                        "distinct_topics",
                        "new_information",
                        "easy_to_answer",
                        "relevant_to_round",
                    )
                ):
                    self.store.save_guidance(self.run_id, number, proposed["parsed"]["questions"])
                    break
                feedback = judged["corrections"]
            else:
                raise Halt(
                    "paused",
                    "Could not prepare distinct, relevant round questions; research has not advanced",
                )
        if self.store.await_guidance(self.run_id, number):
            raise Halt("awaiting_answers", f"Answer the pending questions before round {number}")
        return self.store.guidance(self.run_id, number)

    async def research(self):
        await self.memory.advance()
        run = self.store.run(self.run_id)
        if not run["brief"] or not json.loads(run.get("contract") or "{}"):
            self.stage("intake")
            intake = await self.call(
                "MB",
                "mb-intake" if not run["brief"] else "mb-contract",
                INTAKE,
                {"owner_problem": run["problem"]},
                schema=Intake,
            )
            self.store.update_run(
                self.run_id, brief=intake["text"], contract=json.dumps(intake["parsed"])
            )
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
                    raise Halt("complete", reason)
            number = run["round"] + 1
            if number > self.config.research.max_rounds:
                raise Halt("complete", "Configured round limit reached")
            prefix = f"round-{number}-revision-{run['revision']}"
            previous = history[-1] if history else {}
            phase = phase_for(history, run["revision"])
            answers = (
                await self.round_questions(number, history)
                if self.config.research.mode == "guided"
                else []
            )
            self.check()
            self.emit(
                f"Round {number}/{self.config.research.max_rounds} · RB court",
                kind="round_started",
                payload={
                    "number": number,
                    "max_rounds": self.config.research.max_rounds,
                    "revision": run["revision"],
                    "focus": previous.get("review", {}).get("next_prompt") or run["problem"],
                    "previous_review": previous.get("review", {}),
                    "roles": [RB_ROLES[i % len(RB_ROLES)] for i in range(self.config.rb.agents)],
                    "phase": phase,
                },
            )
            data = {
                "problem": run["problem"],
                "brief": run["brief"],
                "previous_plan": previous.get("plan", "First iteration"),
                "previous_review": previous.get("review", "No previous review"),
                "phase": phase,
                "current_round_answers": answers,
            }
            researchers = await self.group(
                [
                    self.call(
                        "RB",
                        f"{prefix}-rb-{i}",
                        RB_ROLES[i % len(RB_ROLES)]
                        + f"\nIndependent proposal {i + 1}. Investigate only the core question relevant to your role in this phase. Give a short conclusion, cited online evidence, at most three material unknowns and the next online check.",
                        data,
                        search=True,
                    )
                    for i in range(self.config.rb.agents)
                ]
            )
            self.stage(
                "synthesis",
                number,
                agents=len(researchers),
                focus=previous.get("review", {}).get("next_prompt") or run["problem"],
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
            self.stage(
                "critique",
                number,
                agents=self.config.jb.agents,
                plan=synthesis["text"],
                update=synthesis.get("update", {}),
                roles=[JB_ROLES[i % len(JB_ROLES)] for i in range(self.config.jb.agents)],
            )
            judging = {
                "problem": run["problem"],
                "brief": run["brief"],
                "plan": synthesis["text"],
                "research_update": synthesis.get("update", {}),
                "current_round_answers": answers,
                "previous_review": previous.get("review", {}),
                "phase": phase,
            }
            critics = await self.group(
                [
                    self.call(
                        "JB",
                        f"{prefix}-jb-{i}",
                        JB_ROLES[i % len(JB_ROLES)]
                        + f"\nIndependent critique {i + 1}. Check scope, owner constraints and current-phase evidence. Identify at most three decision-changing objections with online checks. Avoid speculative risk lists and requests for human experiments.",
                        judging,
                        search=True,
                    )
                    for i in range(self.config.jb.agents)
                ]
            )
            self.stage(
                "verdict",
                number,
                agents=len(critics),
                critiques=[r["text"][:400] for r in critics],
                update=synthesis.get("update", {}),
            )
            chair = await self.call(
                "JB",
                f"{prefix}-verdict",
                JUDGE,
                judging | {"independent_critiques": [r["text"] for r in critics]},
                structured=True,
            )
            for key in ("summary", "changes", "next_step"):
                if not chair["review"].get(key):
                    chair["review"][key] = synthesis.get("update", {}).get(
                        key, [] if key == "changes" else ""
                    )
            review = chair["review"]
            contract = json.loads(run.get("contract") or "{}")
            missing = [
                item["id"]
                for item in contract.get("deliverables", [])
                if item["id"] not in review["deliverables"]
            ]
            for item in contract.get("deliverables", []):
                status = review["deliverables"].get(item["id"])
                quote = review["deliverable_evidence"].get(item["id"], "").strip()
                if status not in ("complete", "partial", "blocked", "unverifiable") or (
                    status == "complete"
                    and (
                        not quote
                        or " ".join(quote.split()).casefold()
                        not in " ".join(synthesis["text"].split()).casefold()
                    )
                ):
                    missing.append(item["id"])
                if item.get("kind") == "creative" and status in ("blocked", "unverifiable"):
                    missing.append(item["id"] + " requires a conditional draft")
            unapplied = [
                item["question_id"]
                for item in answers
                if not review["guidance_application"].get(item["question_id"], "").strip()
                or not review["guidance_evidence"].get(item["question_id"], "").strip()
                or " ".join(
                    review["guidance_evidence"].get(item["question_id"], "").split()
                ).casefold()
                not in " ".join(synthesis["text"].split()).casefold()
            ]
            if missing or unapplied or not review["guidance_respected"]:
                review["verdict"] = "revise"
                review["decision_ready"] = False
                if missing:
                    review["blocking_issues"] = [
                        "Missing requested deliverable status: " + ", ".join(missing)
                    ] + review["blocking_issues"][:2]
                if unapplied or not review["guidance_respected"]:
                    review["guidance_respected"] = False
                    review["blocking_issues"] = [
                        "Owner answers must be applied to the actual plan: " + ", ".join(unapplied)
                    ] + review["blocking_issues"][:2]
            self.store.save_round(
                self.run_id, number, run["revision"], synthesis["text"], chair["review"]
            )
            self.store.publish_round(
                self.run_id, number, run["revision"], synthesis["text"], chair["review"]
            )
            await self.memory.advance()
            export_run(self.store, self.run_id, self.store.export_directory(self.run_id))
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
            self.emit(
                message,
                kind="research_error",
                payload={
                    "error": message,
                    "diagnostics": exc.diagnostics if isinstance(exc, ClientError) else {},
                },
            )
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
