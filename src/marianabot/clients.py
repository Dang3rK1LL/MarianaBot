"""Official CLI integrations. Prompts travel over stdin; no shell executes model text."""

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from marianabot import __version__
from marianabot.config import Config
from marianabot.limits import SubscriptionLimits
from marianabot.usage import UsageStream

MAX_CAPTURE = 12 * 1024 * 1024


class ClientError(Exception):
    def __init__(self, message: str, retryable: bool = False, limited: bool = False):
        super().__init__(message)
        self.retryable, self.limited = retryable, limited
        self.diagnostics: dict = {}


def subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.startswith(("OPENAI_", "ANTHROPIC_", "CLAUDE_CODE_USE_")) or upper in {
            "CODEX_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDECODE",
            "MARIANA_DISCORD_BOT_TOKEN",
            "DISCORD_BOT_TOKEN",
        }:
            env.pop(key)
    env.update(
        CLAUDE_CODE_DISABLE_FAST_MODE="1",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_BACKGROUND_TASKS="1",
        CLAUDE_CODE_SAFE_MODE="1",
    )
    return env


def executable(command: str) -> list[str]:
    found = shutil.which(command)
    if not found:
        raise ClientError(f"Client not found: {command}. Install the official CLI and run doctor.")
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        # Resolve the official npm package without cmd.exe or shell quoting.
        root = path.parent / "node_modules" / "@openai" / "codex"
        if path.stem.lower() == "codex":
            candidates = list(root.glob("node_modules/@openai/codex-*/vendor/*/codex/codex.exe"))
            candidates += list(root.glob("vendor/*/codex/codex.exe"))
            if len(candidates) == 1:
                return [str(candidates[0])]
            script = root / "bin" / "codex.js"
            node = shutil.which("node")
            if script.is_file() and node:
                return [node, str(script)]
        raise ClientError("Use a native executable path; shell-script launchers are not supported.")
    return [str(path)]


async def launch(args: list[str], cwd: Path, *, env: dict[str, str] | None = None):
    kwargs = (
        {"creationflags": subprocess.CREATE_NO_WINDOW}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=subscription_env() if env is None else env,
        limit=MAX_CAPTURE,
        **kwargs,
    )


async def terminate(process):
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def capture(args: list[str], cwd: Path, timeout: float = 30) -> tuple[int, bytes, bytes]:
    process = await launch(args, cwd)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        if len(stdout) + len(stderr) > MAX_CAPTURE:
            raise ClientError("Client diagnostics exceeded the capture limit")
        return process.returncode, stdout, stderr
    finally:
        await terminate(process)


async def drain(stream):
    # Keep a small in-memory tail for startup errors; never write raw client logs.
    tail = b""
    while chunk := await stream.read(8192):
        tail = (tail + chunk)[-4096:]
    return tail.decode("utf-8", errors="replace")


def diagnostic(text: str) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"(?i)(sk-[a-z0-9_-]+|bearer\s+\S+|eyJ[a-zA-Z0-9_.-]{20,})", "[redacted]", text)
    return text.strip()[-1000:]


class CodexAccount:
    def __init__(self, config: Config, cwd: Path):
        self.config, self.cwd = config, cwd

    async def snapshot(self, include_models: bool = False) -> dict:
        args = executable(self.config.subscription.codex_command) + [
            "-c",
            'forced_login_method="chatgpt"',
            "app-server",
            "--listen",
            "stdio://",
        ]
        process = await launch(args, self.cwd)
        stderr_task = asyncio.create_task(drain(process.stderr))
        request_id = 0

        async def send(payload):
            process.stdin.write((json.dumps(payload) + "\n").encode())
            await process.stdin.drain()

        async def rpc(method, params=None):
            nonlocal request_id
            request_id += 1
            await send({"id": request_id, "method": method, "params": params or {}})
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise ClientError("Codex account service closed before responding")
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("id") == request_id:
                    if "error" in event:
                        raise ClientError(
                            f"Codex {method} failed; check login, network and CLI version"
                        )
                    return event.get("result", {})

        try:
            async with asyncio.timeout(40):
                await rpc(
                    "initialize", {"clientInfo": {"name": "marianabot", "version": __version__}}
                )
                await send({"method": "initialized", "params": {}})
                account = await rpc("account/read", {"refreshToken": True})
                account_data = account.get("account") or {}
                if account_data.get("type") != "chatgpt":
                    raise ClientError("Codex must be signed in with ChatGPT. Run codex login.")
                limits = await rpc("account/rateLimits/read")
                result = {"auth": "chatgpt", "plan": account_data.get("planType"), "limits": limits}
                if include_models:
                    models, details, cursor = [], [], None
                    for _ in range(20):
                        page = await rpc(
                            "model/list", {"cursor": cursor, "limit": 100, "includeHidden": False}
                        )
                        details.extend(page.get("data", []))
                        models.extend(
                            item.get("model") or item.get("id") for item in page.get("data", [])
                        )
                        cursor = page.get("nextCursor")
                        if not cursor:
                            break
                    result["models"] = models
                    result["model_details"] = details
                return result
        except TimeoutError:
            raise ClientError(
                "Codex account check timed out; no model request was sent", retryable=True
            ) from None
        finally:
            await terminate(process)
            await stderr_task


async def claude_account(config: Config, cwd: Path) -> dict:
    code, output, _ = await capture(
        executable(config.subscription.claude_command) + ["auth", "status", "--json"], cwd
    )
    try:
        payload = json.loads(output)
    except ValueError:
        raise ClientError(
            "Claude auth status did not return JSON; update the official CLI"
        ) from None
    if code or not payload.get("loggedIn") or payload.get("authMethod") != "claude.ai":
        raise ClientError(
            "Claude must be signed in with your Claude subscription. Run claude auth login."
        )
    return {"auth": "claude.ai", "plan": payload.get("subscriptionType")}


def error_from(text: str) -> ClientError:
    # Classify the error fields, not unrelated usage/permission metadata or
    # quoted research in a whole event. Keep only bounded, redacted diagnostics.
    try:
        event = json.loads(text)
    except ValueError:
        event = None
    details = {}
    if isinstance(event, dict):
        for name in ("type", "subtype", "status", "status_code", "code", "request_id"):
            if isinstance(event.get(name), (str, int)):
                details[name] = diagnostic(str(event[name]))
        error = event.get("error")
        if isinstance(error, dict):
            details["error"] = {
                key: diagnostic(str(error[key]))
                for key in ("type", "code", "status", "status_code", "message")
                if isinstance(error.get(key), (str, int))
            }
        elif isinstance(error, str):
            details["error"] = diagnostic(error)
        messages = event.get("errors")
        if isinstance(messages, list):
            details["errors"] = [
                diagnostic(item) for item in messages[:10] if isinstance(item, str)
            ]
        for name in ("message", "result"):
            if isinstance(event.get(name), str):
                details[name] = diagnostic(event[name])
        lower = json.dumps(details).lower()
    else:
        details["message"] = diagnostic(text)
        lower = text.lower()
    status = re.search(r"\b(400|401|402|403|404|413|429|500|502|503|504|529)\b", lower)
    status = int(status[1]) if status else None

    def failure(message: str, *, retryable=False, limited=False) -> ClientError:
        error = ClientError(message, retryable=retryable, limited=limited)
        error.diagnostics = details
        return error

    if (
        any(
            word in lower
            for word in (
                "billing_error",
                "insufficient_quota",
                "credit balance",
                "payment required",
                "spend cap",
            )
        )
        or status == 402
    ):
        return failure(
            "Provider billing or credit limit reached; review account settings before resuming"
        )
    if (
        any(
            word in lower
            for word in (
                "rate_limit",
                "rate limit",
                "usage limit",
                "usage_limit",
                "limit reached",
                "hit your limit",
            )
        )
        or status == 429
    ):
        return failure("Subscription usage limit reached", limited=True)
    if any(word in lower for word in ("auth", "login", "unauthorized")) or status in (401, 403):
        return failure("Client authentication or permission failed; check your subscription access")
    if any(
        word in lower for word in ("model_not_found", "model not found", "not supported")
    ) or re.search(r"model[^\n]{0,100}not available", lower):
        return failure(
            "Requested model is unavailable for this account; no fallback model was selected"
        )
    if "error_max_turns" in lower:
        return failure("Claude reached its turn limit before completing the response")
    if "error_max_budget_usd" in lower:
        return failure("Claude reached its configured spending limit; review client settings")
    if "error_max_structured_output_retries" in lower:
        return failure("Claude exhausted its structured-output retries")
    if (
        any(
            word in lower
            for word in ("prompt is too long", "prompt too long", "context_length_exceeded")
        )
        or status == 413
    ):
        return failure("Provider rejected an oversized request; original research is retained")
    if "invalid_request_error" in lower or status == 400:
        return failure("Provider rejected the request format; see saved client diagnostics")
    if any(
        word in lower
        for word in (
            "overloaded",
            "api_error",
            "server_error",
            "server error",
            "timeout",
            "timed out",
            "connection",
            "no response from api",
            "temporarily unavailable",
            "error_during_execution",
        )
    ) or status in (500, 502, 503, 504, 529):
        return failure("Provider temporarily unavailable", retryable=True)
    return failure(
        "Client request failed; inspect account access and client version with mariana doctor"
    )


class NativeClient:
    def __init__(self, provider: str, config: Config, cwd: Path, limits: SubscriptionLimits):
        self.provider, self.config, self.cwd, self.limits = provider, config, cwd, limits
        cwd.mkdir(parents=True, exist_ok=True)

    def args(self, search: bool, *, preferences: bool = True) -> list[str]:
        if self.provider == "openai":
            brain = self.config.rb
            args = executable(self.config.subscription.codex_command) + [
                "exec",
                "--ignore-user-config",
                "-c",
                'forced_login_method="chatgpt"',
                "-c",
                'model_provider="openai"',
                "-c",
                'approval_policy="never"',
                "-c",
                f'web_search="{"live" if search else "disabled"}"',
                "-c",
                "project_doc_max_bytes=0",
                "--json",
                "--ephemeral",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                brain.model,
            ]
            if brain.effort != "auto":
                args += ["-c", f'model_reasoning_effort="{brain.effort}"']
            for feature in (
                "shell_tool",
                "unified_exec",
                "apps",
                "plugins",
                "hooks",
                "multi_agent",
                "browser_use",
                "computer_use",
                "image_generation",
                "memories",
                "goals",
            ):
                args += ["--disable", feature]
            return args + ["-"]
        brain = self.config.jb
        args = executable(self.config.subscription.claude_command) + [
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--safe-mode",
            "--restricted",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--tools",
            "WebSearch" if search else "",
            "--allowedTools",
            "WebSearch" if search else "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--no-chrome",
            "--disable-slash-commands",
        ]
        if preferences:
            args += ["--model", brain.model]
            if brain.effort != "auto":
                args += ["--effort", brain.effort]
        return args

    async def complete(self, prompt: str, search: bool = False, *, on_usage=None) -> dict:
        process = await launch(self.args(search), self.cwd)
        stderr_task = asyncio.create_task(drain(process.stderr))
        result = None
        messages, usage, sources = [], {}, []
        failure = None
        assistant_failure = None
        failed = None
        streamed_text = []
        total = 0
        tokens = UsageStream(self.provider)
        try:
            async with asyncio.timeout(self.config.research.request_timeout_seconds):
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
                while line := await process.stdout.readline():
                    total += len(line)
                    if total > MAX_CAPTURE:
                        raise ClientError("Client output exceeded the capture limit")
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    kind = event.get("type")
                    reported = tokens.observe(event)
                    if reported and on_usage:
                        on_usage(*reported)
                    if self.provider == "openai":
                        if kind == "item.completed":
                            item = event.get("item", {})
                            if item.get("type") == "agent_message":
                                messages.append(item.get("text", ""))
                            if item.get("type") == "web_search":
                                sources.append(item)
                        elif kind == "turn.completed":
                            failure = None
                            usage = event.get("usage", {})
                            result = {
                                "text": messages[-1] if messages else "",
                                "usage": usage,
                                "sources": sources,
                            }
                        elif kind in ("turn.failed", "error"):
                            failure = error_from(json.dumps(event))
                    else:
                        if kind == "rate_limit_event":
                            self.limits.claude(event.get("rate_limit_info", {}))
                        elif kind == "system" and event.get("subtype") == "init":
                            if event.get("model") != self.config.jb.model:
                                raise ClientError(
                                    "Claude selected a different model; stopping to preserve your model choice"
                                )
                            # apiKeySource may say "none"; any actual key source is rejected.
                            if event.get("apiKeySource") not in (None, "none", ""):
                                raise ClientError(
                                    "Claude reported API-key authentication; subscription mode refuses it"
                                )
                        elif kind == "result":
                            if event.get("is_error") or event.get("subtype") != "success":
                                failure = error_from(json.dumps(event))
                                if assistant_failure is not None:
                                    assistant_failure.diagnostics["result_error"] = (
                                        failure.diagnostics
                                    )
                                    failure = assistant_failure
                                result = None
                            elif assistant_failure is not None:
                                # Some CLI versions report a successful agent-loop
                                # exit even though the last assistant turn was an
                                # API error. That text is not a finished review.
                                failure = assistant_failure
                                result = None
                            else:
                                failure = None
                                text = event.get("result", "")
                                if event.get("structured_output") is not None:
                                    text = json.dumps(event["structured_output"])
                                result = {
                                    "text": text,
                                    "usage": event.get("usage", {}),
                                    "sources": sources,
                                    "api_equivalent_usd": event.get("total_cost_usd"),
                                }
                        elif kind == "assistant":
                            text = "\n".join(
                                block.get("text", "")
                                for block in event.get("message", {}).get("content", [])
                                if block.get("type") == "text"
                            )
                            if text:
                                messages.append(text)
                            streamed_text.clear()
                            assistant_failure = (
                                error_from(
                                    json.dumps(
                                        {
                                            "type": "assistant",
                                            "error": event["error"],
                                            "message": text,
                                        }
                                    )
                                )
                                if event.get("error")
                                else None
                            )
                            if assistant_failure is not None:
                                failure = assistant_failure
                            for block in event.get("message", {}).get("content", []):
                                if (
                                    block.get("type") == "tool_use"
                                    and block.get("name") == "WebSearch"
                                ):
                                    sources.append(
                                        {"tool": "WebSearch", "input": block.get("input")}
                                    )
                        elif kind == "error":
                            failure = error_from(json.dumps(event))
                        elif kind == "stream_event":
                            streamed = event.get("event", {})
                            if streamed.get("type") == "error":
                                failure = error_from(json.dumps(streamed))
                            delta = streamed.get("delta", {})
                            if delta.get("type") == "text_delta":
                                streamed_text.append(delta.get("text", ""))
                code = await process.wait()
                if failure:
                    raise failure
                if code or not result:
                    details = diagnostic(await stderr_task)
                    if details:
                        raise error_from(details)
                    raise ClientError("Client ended without a completed response", retryable=True)
                if not result["text"].strip():
                    raise ClientError("Client returned an empty final response", retryable=True)
                result["model"] = (
                    self.config.rb.model if self.provider == "openai" else self.config.jb.model
                )
                if tokens.latest is not None:
                    result["token_usage"] = tokens.latest
                return result
        except TimeoutError:
            failed = ClientError(
                "Client request timed out; provider usage may already have occurred", retryable=True
            )
            failed.diagnostics["timeout"] = True
            raise failed from None
        except ClientError as exc:
            failed = exc
            raise
        finally:
            await terminate(process)
            stderr = await stderr_task
            if failed is not None:
                failed.diagnostics.update(
                    provider=self.provider,
                    exit_code=process.returncode,
                    partial_text="\n".join(messages + ["".join(streamed_text)]).strip(),
                    partial_sources=sources,
                    partial_token_usage=tokens.latest,
                )
                if stderr:
                    failed.diagnostics["stderr"] = diagnostic(stderr)


async def _claude_metadata(config: Config, cwd: Path, *, usage=False) -> dict:
    """Only initialize and read metadata; never submit a user message."""
    account = await claude_account(config, cwd)
    args = NativeClient("anthropic", config, cwd, None).args(False, preferences=False)
    env = subscription_env()
    if usage:
        # This switch also blocks explicit /usage reads in Claude Code. Relax it
        # only for this metadata process; research clients keep the usual isolation.
        env.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)
        env.update(DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1", DISABLE_AUTOUPDATER="1")
    process = await launch(args + ["--input-format", "stream-json"], cwd, env=env)
    stderr_task = asyncio.create_task(drain(process.stderr))

    async def rpc(subtype, **extra):
        request = {
            "type": "control_request",
            "request_id": f"mariana-{subtype}",
            "request": {"subtype": subtype, **extra},
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        while line := await process.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            response = event.get("response", {})
            if (
                event.get("type") == "control_response"
                and response.get("request_id") == request["request_id"]
            ):
                data = response.get("response")
                if response.get("subtype") == "success" and isinstance(data, dict):
                    return data
                break
        raise ClientError("Claude metadata unavailable; check login, network and CLI version")

    try:
        async with asyncio.timeout(25):
            metadata = await rpc("initialize", hooks={})
            if usage:
                metadata = await rpc("get_usage", skip_behaviors=True)
            return account | {"metadata": metadata}
    except TimeoutError:
        raise ClientError("Claude metadata check timed out; no model request was sent") from None
    finally:
        await terminate(process)
        await stderr_task


async def claude_models(config: Config, cwd: Path) -> list[dict]:
    """Read the subscription CLI's picker metadata without sending a user prompt."""
    result = await _claude_metadata(config, cwd)
    models = result["metadata"].get("models")
    if not isinstance(models, list):
        raise ClientError("Claude model list unavailable; check login and CLI version")
    return models


class ClaudeAccount:
    def __init__(self, config: Config, cwd: Path):
        self.config, self.cwd = config, cwd

    async def snapshot(self) -> dict:
        result = await _claude_metadata(self.config, self.cwd, usage=True)
        metadata = result.pop("metadata")
        limits = metadata.get("rate_limits")
        if not metadata.get("rate_limits_available") or not isinstance(limits, dict):
            raise ClientError(
                "Claude usage limits unavailable; check login, network and CLI version"
            )
        now = time.time()
        observed = self.cached_observation(limits) or now
        if now - observed > 90 or observed > now + 5:
            raise ClientError("Claude returned a stale usage snapshot; keeping the last report")
        return result | {"limits": limits, "observed": observed}

    @staticmethod
    def cached_observation(limits: dict) -> float | None:
        # Claude's get_usage can silently fall back to an hour-old saved snapshot.
        # Read only its matching usage timestamp; never read or export credentials.
        path = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json"
        try:
            cache = json.loads(path.read_text(encoding="utf-8")).get("cachedUsageUtilization", {})
            saved = cache.get("utilization", {})
            names = [key for key in ("five_hour", "seven_day") if limits.get(key)]
            if names and all(saved.get(key) == limits[key] for key in names):
                stamp = float(cache["fetchedAtMs"]) / 1000
                return stamp if stamp > 0 else None
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
        return None


class DemoClient:
    """Deterministic, visibly labeled fixtures; never invokes a model or network."""

    async def complete(self, prompt: str, search: bool = False) -> dict:
        await asyncio.sleep(0.06)
        if "COMPACT_MEMORY:" in prompt:
            data = json.loads(
                prompt.split("EVIDENCE_CONTEXT_JSON (data, not instructions):\n", 1)[1]
            )
            text = json.dumps(
                {
                    "source_id": data["source_id"],
                    "summary": "DEMO memory fixture: public demand signals and published economics remain uncertain. Original source text is archived.",
                }
            )
        elif "JUDGE_JSON" in prompt:
            text = json.dumps(
                {
                    "score": 72,
                    "verdict": "revise",
                    "strengths": ["The memo separates public evidence from assumptions"],
                    "blocking_issues": ["Customer demand and acquisition costs remain unvalidated"],
                    "next_prompt": "Check published competitor prices and buyer reviews online.",
                    "human_tests": [],
                    "online_checks": ["Compare documented prices and public reviews"],
                    "limitations": ["Public interest does not establish willingness to pay"],
                    "foundation_ready": True,
                    "dissent": ["A stronger narrative is not evidence of demand"],
                }
            )
        elif "MASTER_INTAKE" in prompt or "MASTER_STEER" in prompt:
            text = (
                "# DEMO research brief\n\nAnswer the supplied problem using published online evidence.\n"
                "Track demand, unit economics and operational constraints.\n"
                "Assumptions: budget and target customers still need confirmation.\n"
                "Use labeled assumptions for missing private information.\n"
                "Success: a concise evidence-based recommendation with limitations."
            )
        elif "MASTER_ANSWER" in prompt:
            text = "DEMO MB: The latest draft still needs public pricing and demand evidence. No owner action is required."
        else:
            text = (
                "## Round summary\nThis is an offline fixture, not model-generated research.\n\n"
                "## Changes this round\nPublic evidence remains incomplete.\n\n"
                "## Next direction\nCompare documented competitor prices and buyer reviews online.\n\n"
                "Unknowns: willingness to pay, acquisition costs and local requirements.\n"
                "Limitation: online interest does not establish willingness to pay.\n"
                "Sources: none; illustrative demo only."
            )
        return {
            "text": text,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "sources": [],
            "model": "offline-demo",
        }
